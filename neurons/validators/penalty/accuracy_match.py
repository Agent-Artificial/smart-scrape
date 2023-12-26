import torch
import re
from typing import List
from template.protocol import TwitterPromptAnalysisResult
from utils.tasks import Task
from penalty import PenaltyModelType, BasePenaltyModel

class AccuracyPenaltyModel(BasePenaltyModel):
    """
    A model for calculating accuracy penalties based on the match between
    the provided completions and the expected query criteria.

    Attributes:
        max_penalty: The maximum penalty that can be applied to a completion.
    """
    def __init__(self, max_penalty: float):
        """
        Initializes the AccuracyPenaltyModel with a specified maximum penalty.

        Args:
            max_penalty: The maximum penalty that can be applied to a completion.
        """
        super().__init__(max_penalty)

    @property
    def name(self) -> str:
        """
        Returns the name of the penalty model.

        Returns:
            The name of the penalty model as defined in PenaltyModelType.
        """
        return PenaltyModelType.accuracy_match_penalty.value

    def _compile_patterns(self, prompt_analysis: TwitterPromptAnalysisResult):
        """
        Compiles regular expression patterns for keywords, hashtags, and user mentions
        based on the provided TwitterPromptAnalysisResult.

        Args:
            prompt_analysis: The TwitterPromptAnalysisResult containing the query criteria.
        """
        keyword_pattern = '|'.join(re.escape(keyword) for keyword in prompt_analysis.keywords)
        hashtag_pattern = '|'.join(re.escape('#' + hashtag) for hashtag in prompt_analysis.hashtags)
        user_pattern = '|'.join(re.escape('@' + user) for user in prompt_analysis.user_mentions)

        self.keyword_regex = re.compile(keyword_pattern, re.IGNORECASE) if prompt_analysis.keywords else None
        self.hashtag_regex = re.compile(hashtag_pattern, re.IGNORECASE) if prompt_analysis.hashtags else None
        self.user_regex = re.compile(user_pattern, re.IGNORECASE) if prompt_analysis.user_mentions else None

    def calculate_penalties(self, task: Task, completions: List[str]) -> torch.FloatTensor:
        """
        Calculates the penalties for each completion based on the absence of
        keywords, hashtags, or user mentions as defined in the task's query result.

        Args:
            task: The task containing the query criteria.
            completions: A list of strings representing the completed texts.

        Returns:
            A tensor of penalties for each completion.
        """
        prompt = task.base_text
        self._compile_patterns(task.prompt_analysis)
        penalties = []
        for completion in completions:
            penalty = 0.0
            if self.keyword_regex and not self.keyword_regex.search(completion):
                penalty += self.max_penalty / 3
            if self.hashtag_regex and not self.hashtag_regex.search(completion):
                penalty += self.max_penalty / 3
            if self.user_regex and not self.user_regex.search(completion):
                penalty += self.max_penalty / 3
            penalties.append(penalty)
        return torch.tensor(penalties, dtype=torch.float32)

    def calculate_penalties_from_query(self, prompt_analysis: TwitterPromptAnalysisResult, completions: List[str]) -> torch.FloatTensor:
        """
        Calculates the penalties for each completion based on the absence of
        keywords, hashtags, or user mentions as defined in the provided query result.

        Args:
            prompt_analysis: The TwitterPromptAnalysisResult containing the query criteria.
            completions: A list of strings representing the completed texts.

        Returns:
            A tensor of penalties for each completion.
        """
        self._compile_patterns(prompt_analysis)
        penalties = []
        for completion in completions:
            penalty = 0.0
            if self.keyword_regex and not self.keyword_regex.search(completion):
                penalty += self.max_penalty / 3
            if self.hashtag_regex and not self.hashtag_regex.search(completion):
                penalty += self.max_penalty / 3
            if self.user_regex and not self.user_regex.search(completion):
                penalty += self.max_penalty / 3
            penalties.append(penalty)
        return torch.tensor(penalties, dtype=torch.float32)

