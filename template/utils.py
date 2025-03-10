import re
import os
import ast
import math
import json
import wandb
import base64
import random
import asyncio
import template
import copy
import torch
import requests
import traceback
import bittensor as bt
import threading
import multiprocessing
import aiohttp
from . import client
from collections import deque
from datetime import datetime
from template.misc import ttl_get_block

list_update_lock = asyncio.Lock()
_text_questions_buffer = deque()


def load_state_from_file(filename="validators/state.json"):
    if os.path.exists(filename):
        with open(filename, "r") as file:
            bt.logging.info("loaded previous state")
            return json.load(file)
    else:
        bt.logging.info("initialized new global state")
        return {
            "text": {
                "themes": None,
                "questions": None,
                "theme_counter": 0,
                "question_counter": 0,
            },
            "images": {
                "themes": None,
                "questions": None,
                "theme_counter": 0,
                "question_counter": 0,
            },
        }


state = load_state_from_file()


def get_state():
    global state
    if state is None:
        load_state_from_file()
    return state


def save_state_to_file(state, filename="state.json"):
    with open(filename, "w") as file:
        bt.logging.success(f"saved global state to {filename}")
        json.dump(state, file)


def preprocess_string(text):
    processed_text = text.replace("\t", "")
    placeholder = "___SINGLE_QUOTE___"
    processed_text = re.sub(r"(?<=\w)'(?=\w)", placeholder, processed_text)
    processed_text = processed_text.replace("'", '"').replace(placeholder, "'")

    # First, remove all comments, ending at the next quote
    no_comments_text = ""
    i = 0
    in_comment = False
    while i < len(processed_text):
        if processed_text[i] == "#":
            in_comment = True
        elif processed_text[i] == '"' and in_comment:
            in_comment = False
            no_comments_text += processed_text[
                i
            ]  # Keep the quote that ends the comment
            i += 1
            continue
        if not in_comment:
            no_comments_text += processed_text[i]
        i += 1

    # Now process the text without comments for quotes
    cleaned_text = []
    inside_quotes = False
    found_first_bracket = False

    i = 0
    while i < len(no_comments_text):
        char = no_comments_text[i]

        if not found_first_bracket:
            if char == "[":
                found_first_bracket = True
            cleaned_text.append(char)
            i += 1
            continue

        if char == '"':
            # Look for preceding comma or bracket, skipping spaces
            preceding_char_index = i - 1
            found_comma_or_bracket = False

            while preceding_char_index >= 0:
                if (
                    no_comments_text[preceding_char_index] in "[,"
                ):  # Check for comma or opening bracket
                    found_comma_or_bracket = True
                    break
                elif (
                    no_comments_text[preceding_char_index] not in " \n"
                ):  # Ignore spaces and new lines
                    break
                preceding_char_index -= 1

            following_char_index = i + 1
            while (
                following_char_index < len(no_comments_text)
                and no_comments_text[following_char_index] in " \n"
            ):
                following_char_index += 1

            if found_comma_or_bracket or (
                following_char_index < len(no_comments_text)
                and no_comments_text[following_char_index] in "],"
            ):
                inside_quotes = not inside_quotes
            else:
                i += 1
                continue  # Skip this quote

            cleaned_text.append(char)
            i += 1
            continue

        if char == " ":
            # Skip spaces if not inside quotes and if the space is not between words
            if not inside_quotes and (
                i == 0
                or no_comments_text[i - 1] in " ,["
                or no_comments_text[i + 1] in " ,]"
            ):
                i += 1
                continue

        cleaned_text.append(char)
        i += 1

    cleaned_str = "".join(cleaned_text)
    cleaned_str = re.sub(r"\[\s+", "[", cleaned_str)
    cleaned_str = re.sub(r"\s+\]", "]", cleaned_str)
    cleaned_str = re.sub(
        r"\s*,\s*", ", ", cleaned_str
    )  # Ensure single space after commas

    start, end = cleaned_str.find("["), cleaned_str.rfind("]")
    if start != -1 and end != -1 and end > start:
        cleaned_str = cleaned_str[start : end + 1]

    return cleaned_str


def convert_to_list(text):
    pattern = r"\d+\.\s"
    items = [item.strip() for item in re.split(pattern, text) if item]
    return items


def extract_python_list(text: str):
    try:
        if re.match(r"\d+\.\s", text):
            return convert_to_list(text)

        bt.logging.debug(f"Preprocessed text = {text}")
        text = preprocess_string(text)
        bt.logging.debug(f"Postprocessed text = {text}")

        # Extracting list enclosed in square brackets
        match = re.search(r'\[((?:[^][]|"(?:\\.|[^"\\])*")*)\]', text, re.DOTALL)
        if match:
            list_str = match.group(1)

            # Using ast.literal_eval to safely evaluate the string as a list
            evaluated = ast.literal_eval("[" + list_str + "]")
            if isinstance(evaluated, list):
                return evaluated

    except Exception as e:
        bt.logging.error(
            f"Unexpected error when extracting list: {e}\n{traceback.format_exc()}"
        )

    return None


async def call_openai(messages, temperature, model, seed=1234, response_format=None):
    for attempt in range(2):
        bt.logging.trace(
            f"Calling Openai. Temperature = {temperature}, Model = {model}, Seed = {seed},  Messages = {messages}"
        )
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                seed=seed,
                response_format=response_format,
            )
            response = response.choices[0].message.content
            bt.logging.trace(f"validator response is {response}")
            return response

        except Exception as e:
            bt.logging.error(f"Error when calling OpenAI: {e}")
            await asyncio.sleep(0.5)

    return None


# Github unauthorized rate limit of requests per hour is 60. Authorized is 5000.
def get_version(line_number=22):
    url = f"https://api.github.com/repos/surcyf123/smart-scrape/contents/template/__init__.py"
    response = requests.get(url)
    if response.status_code == 200:
        content = response.json()["content"]
        decoded_content = base64.b64decode(content).decode("utf-8")
        lines = decoded_content.split("\n")
        if line_number <= len(lines):
            version_line = lines[line_number - 1]
            version_match = re.search(r'__version__ = "(.*?)"', version_line)
            if version_match:
                return version_match.group(1)
            else:
                raise Exception("Version information not found in the specified line")
        else:
            raise Exception("Line number exceeds file length")
    else:
        bt.logging.error("github api call failed")
        return None


def send_discord_alert(message, webhook_url):
    data = {"content": f"@everyone {message}", "username": "Subnet22 Updates"}
    try:
        response = requests.post(webhook_url, json=data)
        if response.status_code == 204:
            print("Discord alert sent successfully!")
        else:
            print(f"Failed to send Discord alert. Status code: {response.status_code}")
    except Exception as e:
        print(f"Failed to send Discord alert: {e}", exc_info=True)


def resync_metagraph(self):
    """Resyncs the metagraph and updates the hotkeys and moving averages based on the new metagraph."""
    bt.logging.info("resync_metagraph()")

    # Copies state of metagraph before syncing.
    previous_metagraph = copy.deepcopy(self.metagraph)

    # Sync the metagraph.
    self.metagraph.sync(subtensor=self.subtensor)

    # Check if the metagraph axon info has changed.
    if previous_metagraph.axons == self.metagraph.axons:
        return

    bt.logging.info(
        "Metagraph updated, re-syncing hotkeys, dendrite pool and moving averages"
    )
    # Zero out all hotkeys that have been replaced.
    for uid, hotkey in enumerate(self.hotkeys):
        if hotkey != self.metagraph.hotkeys[uid]:
            self.moving_averaged_scores[uid] = 0  # hotkey has been replaced

    # Check to see if the metagraph has changed size.
    # If so, we need to add new hotkeys and moving averages.
    if len(self.hotkeys) < len(self.metagraph.hotkeys):
        # Update the size of the moving average scores.
        new_moving_average = torch.zeros((self.metagraph.n)).to(self.device)
        min_len = min(len(self.hotkeys), len(self.moving_averaged_scores))
        new_moving_average[:min_len] = self.moving_averaged_scores[:min_len]
        self.moving_averaged_scores = new_moving_average

    # Update the hotkeys.
    self.hotkeys = copy.deepcopy(self.metagraph.hotkeys)


async def save_logs(prompt, logs):
    logging_endpoint_url = os.environ.get("LOGGING_ENDPOINT_URL")

    if not logging_endpoint_url:
        return

    async with aiohttp.ClientSession() as session:
        await session.post(
            logging_endpoint_url,
            json={
                "prompt": prompt,
                "logs": logs,
            },
        )


async def save_logs_from_miner(
    self, synapse, prompt, completion, prompt_analysis, data
):
    if not self.miner.config.miner.save_logs or not prompt_analysis or not data:
        return

    asyncio.create_task(
        save_logs(
            prompt=prompt,
            logs=[
                {
                    "completion": completion,
                    "prompt_analysis": prompt_analysis.dict(),
                    "data": data,
                    "miner_uid": self.miner.my_subnet_uid,
                    "hotkey": synapse.axon.hotkey,
                    "coldkey": next(
                        (
                            axon.coldkey
                            for axon in self.miner.metagraph.axons
                            if axon.hotkey == synapse.axon.hotkey
                        ),
                        None,  # Provide a default value here, such as None or an appropriate placeholder
                    ),
                }
            ],
        )
    )


async def save_logs_in_chunks(self, prompt, responses, uids, rewards, weights):
    try:
        logs = [
            {
                "completion": response.completion,
                "prompt_analysis": response.prompt_analysis.dict(),
                "data": response.miner_tweets,
                "miner_uid": uid,
                "score": reward,
                "hotkey": response.axon.hotkey,
                "coldkey": next(
                    (
                        axon.coldkey
                        for axon in self.metagraph.axons
                        if axon.hotkey == response.axon.hotkey
                    ),
                    None,  # Provide a default value here, such as None or an appropriate placeholder
                ),
                "weight": weights.get(str(uid)),
            }
            for response, uid, reward in zip(responses, uids.tolist(), rewards.tolist())
        ]

        chunk_size = 50

        log_chunks = [logs[i : i + chunk_size] for i in range(0, len(logs), chunk_size)]

        for chunk in log_chunks:
            await save_logs(
                prompt=prompt,
                logs=chunk,
            )
    except Exception as e:
        bt.logging.error(f"Error in save_logs_in_chunks: {e}")
        raise e


def calculate_bonus_score(original_score, link_count, max_bonus=0.2, link_sensitivity=2):
    """
    Calculate the new score with a bonus based on the number of links.

    :param original_score: The original score ranging from 0.1 to 1.
    :param link_count: The number of links in the tweet.
    :param max_bonus: The maximum bonus to add to the score. Default is 0.2.
    :param link_sensitivity: Controls how quickly the bonus grows with the number of links. Higher values mean slower growth.
    :return: The new score with the bonus included.
    """
    # Calculate the bonus
    bonus = max_bonus * (1 - 1 / (1 + link_count / link_sensitivity))
    
    # Ensure the total score does not exceed 1
    new_score = min(1, original_score + bonus)
    
    return new_score