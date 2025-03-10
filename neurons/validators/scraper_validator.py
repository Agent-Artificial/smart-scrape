import math
import torch
import wandb
import random
import json
import bittensor as bt
from base_validator import AbstractNeuron
from template.protocol import ScraperStreamingSynapse, TwitterPromptAnalysisResult
from template.stream import process_async_responses, process_single_response
from reward import RewardModelType, RewardScoringType
from typing import List
from utils.mock import MockRewardModel
import time
from neurons.validators.penalty import (
    TaskValidationPenaltyModel,
    AccuracyPenaltyModel,
    LinkValidationPenaltyModel,
)
from neurons.validators.reward.summary_relevance import SummaryRelevanceRewardModel
from neurons.validators.reward.twitter_content_relevance import (
    TwitterContentRelevanceModel,
)
from neurons.validators.reward.search_content_relevance import (
    WebSearchContentRelevanceModel,
)
from neurons.validators.reward.reward_llm import RewardLLM
from neurons.validators.utils.tasks import TwitterTask

from template.dataset import MockTwitterQuestionsDataset
from template.services.twitter_api_wrapper import TwitterAPIClient
from template.utils import save_logs
from template import QUERY_MINERS
import asyncio


class ScraperValidator:
    def __init__(self, neuron: AbstractNeuron):
        self.streaming = True
        self.query_type = "text"
        self.model = "gpt-3.5-turbo-0125"
        self.weight = 1
        self.seed = 1234
        self.neuron = neuron
        self.timeout = 150
        self.tools = ["Recent Tweets", "Web Search", "Wikipedia Search", "ArXiv Search", "Youtube Search"]

        # Init device.
        bt.logging.debug("loading", "device")
        bt.logging.debug(
            "self.neuron.config.neuron.device = ", str(self.neuron.config.neuron.device)
        )

        self.reward_weights = torch.tensor(
            [
                self.neuron.config.reward.summary_relevance_weight,
                self.neuron.config.reward.twitter_content_weight,
                self.neuron.config.reward.web_search_relavance_weight,
            ],
            dtype=torch.float32,
        ).to(self.neuron.config.neuron.device)

        if self.reward_weights.sum() != 1:
            message = (
                f"Reward function weights do not sum to 1 (Current sum: {self.reward_weights.sum()}.)"
                f"Check your reward config file at `reward/config.py` or ensure that all your cli reward flags sum to 1."
            )
            bt.logging.error(message)
            raise Exception(message)

        self.reward_llm = RewardLLM()
        if (
            self.neuron.config.reward.twitter_content_weight > 0
            or self.neuron.config.reward.summary_relevance_weight > 0
        ) and not self.neuron.config.neuron.is_disable_tokenizer_reward:
            self.reward_llm.init_pipe_zephyr()

        self.reward_functions = [
            (
                SummaryRelevanceRewardModel(
                    device=self.neuron.config.neuron.device,
                    scoring_type=RewardScoringType.summary_relevance_score_template,
                    llm_reward=self.reward_llm,
                )
                if self.neuron.config.reward.summary_relevance_weight > 0
                else MockRewardModel(RewardModelType.summary_relavance_match.value)
            ),
            (
                TwitterContentRelevanceModel(
                    device=self.neuron.config.neuron.device,
                    scoring_type=RewardScoringType.summary_relevance_score_template,
                    llm_reward=self.reward_llm,
                )
                if self.neuron.config.reward.twitter_content_weight > 0
                else MockRewardModel(RewardModelType.link_content_match.value)
            ),
            (
                WebSearchContentRelevanceModel(
                    device=self.neuron.config.neuron.device,
                    scoring_type=RewardScoringType.search_relevance_score_template,
                    llm_reward=self.reward_llm,
                )
                if self.neuron.config.reward.web_search_relavance_weight > 0
                else MockRewardModel(
                    RewardModelType.search_summary_relevance_match.value
                )
            ),
        ]

        self.penalty_functions = [
            # LinkValidationPenaltyModel(max_penalty=0.7),
            # AccuracyPenaltyModel(max_penalty=1),
        ]
        self.twitter_api = TwitterAPIClient()

    async def run_task_and_score(
        self,
        task: TwitterTask,
        strategy=QUERY_MINERS.RANDOM,
        is_only_allowed_miner=True,
        is_intro_text=False,
        specified_uids=None,
        tools=[],
    ):
        task_name = task.task_name
        prompt = task.compose_prompt()

        bt.logging.debug("run_task", task_name)

        # Record event start time.
        event = {"name": task_name, "task_type": task.task_type}
        start_time = time.time()

        # Get random id on that step
        uids = await self.neuron.get_uids(
            strategy=strategy,
            is_only_allowed_miner=is_only_allowed_miner,
            specified_uids=specified_uids,
        )

        axons = [self.neuron.metagraph.axons[uid] for uid in uids]
        synapse = ScraperStreamingSynapse(
            messages=prompt,
            model=self.model,
            seed=self.seed,
            is_intro_text=is_intro_text,
            tools=tools,
        )

        # Make calls to the network with the prompt.
        async_responses = await self.neuron.dendrite.forward(
            axons=axons,
            synapse=synapse,
            timeout=self.timeout,
            streaming=self.streaming,
            deserialize=False,
        )

        return async_responses, uids, event, start_time

    async def compute_rewards_and_penalties(
        self, event, prompt, task, responses, uids, start_time
    ):
        try:
            if not len(uids):
                bt.logging.warning("No UIDs provided for logging event.")
                return

            bt.logging.info("Computing rewards and penalties")

            rewards = torch.zeros(len(responses), dtype=torch.float32).to(
                self.neuron.config.neuron.device
            )
            for weight_i, reward_fn_i in zip(
                self.reward_weights, self.reward_functions
            ):
                start_time = time.time()
                reward_i_normalized, reward_event = reward_fn_i.apply(
                    task.base_text, responses, task.task_name, uids
                )
                rewards += weight_i * reward_i_normalized.to(
                    self.neuron.config.neuron.device
                )
                if not self.neuron.config.neuron.disable_log_rewards:
                    event = {**event, **reward_event}
                execution_time = time.time() - start_time
                bt.logging.trace(str(reward_fn_i.name), reward_i_normalized.tolist())
                bt.logging.info(
                    f"Applied reward function: {reward_fn_i.name} in {execution_time / 60:.2f} minutes"
                )

            for penalty_fn_i in self.penalty_functions:
                raw_penalty_i, adjusted_penalty_i, applied_penalty_i = (
                    penalty_fn_i.apply_penalties(responses, task)
                )
                penalty_start_time = time.time()
                rewards *= applied_penalty_i.to(self.neuron.config.neuron.device)
                penalty_execution_time = time.time() - penalty_start_time
                if not self.neuron.config.neuron.disable_log_rewards:
                    event[penalty_fn_i.name + "_raw"] = raw_penalty_i.tolist()
                    event[penalty_fn_i.name + "_adjusted"] = adjusted_penalty_i.tolist()
                    event[penalty_fn_i.name + "_applied"] = applied_penalty_i.tolist()
                bt.logging.trace(str(penalty_fn_i.name), applied_penalty_i.tolist())
                bt.logging.info(
                    f"Applied penalty function: {penalty_fn_i.name} in {penalty_execution_time:.2f} seconds"
                )

            scattered_rewards = self.neuron.update_moving_averaged_scores(uids, rewards)
            self.log_event(
                task, event, start_time, uids, rewards, prompt=task.compose_prompt()
            )

            scores = torch.zeros(len(self.neuron.metagraph.hotkeys))
            uid_scores_dict = {}
            wandb_data = {
                "modality": "twitter_scrapper",
                "prompts": {},
                "responses": {},
                "scores": {},
                "timestamps": {},
            }
            bt.logging.info(
                f"======================== Reward ==========================="
            )
            # Initialize an empty list to accumulate log messages
            log_messages = []
            for uid_tensor, reward, response in zip(uids, rewards.tolist(), responses):
                uid = uid_tensor.item()
                completion_length = (
                    len(response.completion) if response.completion is not None else 0
                )
                completion_links_length = (
                    len(response.completion_links)
                    if response.completion_links is not None
                    else 0
                )
                # Accumulate log messages instead of logging them immediately
                log_messages.append(
                    f"UID: {uid}, R: {round(reward, 3)}, C: {completion_length}, L: {completion_links_length}"
                )
                bt.logging.trace(f"{response.completion}")

            # Log the accumulated messages in groups of three
            for i in range(0, len(log_messages), 3):
                bt.logging.info(" | ".join(log_messages[i : i + 3]))

            bt.logging.info(
                f"======================== Reward ==========================="
            )

            for uid_tensor, reward, response in zip(uids, rewards.tolist(), responses):
                uid = uid_tensor.item()  # Convert tensor to int
                uid_scores_dict[uid] = reward
                scores[uid] = reward  # Now 'uid' is an int, which is a valid key type
                wandb_data["scores"][uid] = reward
                wandb_data["responses"][uid] = response.completion
                wandb_data["prompts"][uid] = prompt

            await self.neuron.update_scores(
                wandb_data=wandb_data,
                prompt=prompt,
                responses=responses,
                uids=uids,
                rewards=rewards,
            )

            return rewards
        except Exception as e:
            bt.logging.error(f"Error in compute_rewards_and_penalties: {e}")
            raise e

    def log_event(self, task, event, start_time, uids, rewards, prompt):
        event.update(
            {
                "step_length": time.time() - start_time,
                "prompt": prompt,
                "uids": uids.tolist(),
                "rewards": rewards.tolist(),
                "propmt": task.base_text,
            }
        )
        bt.logging.debug("Run Task event:", str(event))

    async def process_async_responses(async_responses):
        tasks = [resp for resp in async_responses]
        responses = await asyncio.gather(*tasks)
        for response in responses:
            stream_text = "".join([chunk[1] for chunk in response if not chunk[0]])
            if stream_text:
                yield stream_text  # Yield stream text as soon as it's available
            # Instead of returning, yield final synapse objects with a distinct flag
            final_synapse = next((chunk[1] for chunk in response if chunk[0]), None)
            if final_synapse:
                yield (True, final_synapse)  # Yield final synapse with a flag

    async def query_and_score(self, strategy=QUERY_MINERS.RANDOM):
        try:
            dataset = MockTwitterQuestionsDataset()
            prompt = dataset.next()

            task_name = "augment"
            task = TwitterTask(
                base_text=prompt,
                task_name=task_name,
                task_type="twitter_scraper",
                criteria=[],
            )

            if not len(self.neuron.available_uids):
                bt.logging.info("No available UIDs, skipping task execution.")
                return

            async_responses, uids, event, start_time = await self.run_task_and_score(
                task=task, 
                strategy=strategy,
                is_only_allowed_miner=False,
                tools=self.tools
            )

            final_synapses = []
            async for value in process_async_responses(async_responses):
                if isinstance(value, bt.Synapse):
                    final_synapses.append(value)
                else:
                    pass

            await self.compute_rewards_and_penalties(
                event=event,
                prompt=prompt,
                task=task,
                responses=final_synapses,
                uids=uids,
                start_time=start_time,
            )
        except Exception as e:
            bt.logging.error(f"Error in query_and_score: {e}")
            raise e

    async def organic(self, query):
        try:
            prompt = query["content"]
            tools = query.get("tools", [])

            task_name = "augment"
            task = TwitterTask(
                base_text=prompt,
                task_name=task_name,
                task_type="twitter_scraper",
                criteria=[],
            )

            if not len(self.neuron.available_uids):
                bt.logging.info("Not available uids")
                raise StopAsyncIteration("Not available uids")

            async_responses, uids, event, start_time = await self.run_task_and_score(
                task=task,
                strategy=QUERY_MINERS.RANDOM,
                is_only_allowed_miner=True,
                is_intro_text=True,
                tools=tools,
            )
            final_synapses = []
            for response in async_responses:
                async for value in response:
                    if isinstance(value, bt.Synapse):
                        final_synapses.append(value)
                    else:
                        yield value

            async def process_and_score_responses():
                await self.compute_rewards_and_penalties(
                    event=event,
                    prompt=prompt,
                    task=task,
                    responses=final_synapses,
                    uids=uids,
                    start_time=start_time,
                )

            asyncio.create_task(process_and_score_responses())
        except Exception as e:
            bt.logging.error(f"Error in organic: {e}")
            raise e

    async def organic_specified(self, query, specified_uids=None):
        try:
            prompt = query["content"]
            tools = query.get("tools", [])

            task_name = "augment"
            task = TwitterTask(
                base_text=prompt,
                task_name=task_name,
                task_type="twitter_scraper",
                criteria=[],
            )

            if not len(self.neuron.available_uids):
                bt.logging.info("Not available uids")
                raise StopAsyncIteration("Not available uids")

            yield f"Contacting miner IDs: {'; '.join(map(str, specified_uids))} \n\n\n"
            async_responses, uids, event, start_time = await self.run_task_and_score(
                task=task,
                strategy=QUERY_MINERS.ALL,
                is_only_allowed_miner=False,
                specified_uids=specified_uids,
                tools=self.tools,
            )
            

            final_synapses = []
            async for value in  process_async_responses(async_responses):
                if isinstance(value, bt.Synapse):
                    final_synapses.append(value)
                else:
                    pass

            for uid_tensor, response in zip(uids, final_synapses):
                yield f"Miner ID: {uid_tensor.item()} Completion Output: \n\n"
                yield "----------------------------------------\n\n"
                yield f"{response.completion}\n\n"
                yield "\n\n======================================================================================================================================================\n\n"

            yield "Initiating scoring system. Please wait for the response... \n\n"
            start_compute_time = time.time()
            rewards_task = asyncio.create_task(self.compute_rewards_and_penalties(
                event=event,
                prompt=prompt,
                task=task,
                responses=final_synapses,
                uids=uids,
                start_time=start_time,
            ))

            while not rewards_task.done():
                await asyncio.sleep(30)  # Check every 30 seconds if the task is done
                elapsed_time = time.time() - start_compute_time
                if elapsed_time > 60:  # If more than one minute has passed
                    yield f"Waiting for reward scoring... {elapsed_time // 60} minutes elapsed.\n\n"
                    start_compute_time = time.time()  # Reset the timer

            rewards = await rewards_task

            yield "\n\n======================================================================================================================================================\n\n"
            for uid_tensor, reward, response in zip(
                uids, rewards.tolist(), final_synapses
            ):
                yield f"Miner ID: {uid_tensor.item()} - Reward: {reward:.2f}\n\n"

            missing_uids = set(specified_uids) - set(uid.item() for uid in uids)
            for missing_uid in missing_uids:
                yield f"No response from Miner ID: {missing_uid}\n"
                yield "----------------------------------------\n\n\n"

        except Exception as e:
            bt.logging.error(f"Error in query_and_score: {e}")
            raise e
