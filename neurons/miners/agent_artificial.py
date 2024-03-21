import os
import json
import requests
from dotenv import load_dotenv
from pydantic import BaseModel


load_dotenv()
class Agent(BaseModel):
    isInferenceUP: bool
    api_key: str
    base_url: str
    model: str

class AgentArtificial(Agent):
    def __init__(self):
        """
        Initializes the object with the necessary attributes for inference.

        This function sets the `isInferenceUP` attribute to the result of the `spot_check` method.
        It sets the `api_key` attribute to the result of the `choose_api_key` method.
        It sets the `base_url` attribute to the result of the `choose_baseURL` method.
        It sets the `model` attribute to the result of the `choose_model` method.

        Parameters:
            None

        Returns:
            None
        """
        self.isInferenceUP = self.spot_check()
        self.api_key = self.choose_api_key()
        self.base_url = self.choose_baseURL()
        self.model = self.choose_model()
    def choose_baseURL(self) -> str:
        """
        Returns the base URL to be used based on the status of the inference.

        Returns:
            str: The base URL to be used. If the inference is up, it returns the value of the 'AGENTARTIFICIAL_BASE_URL' environment variable. Otherwise, it returns the value of the 'OPENAI_BASE_URL' environment variable.
        """
        if self.isInferenceUP:
            return str(os.getenv('AGENTARTIFICIAL_BASE_URL'))
        else: 
            return str(os.getenv('OPENAI_BASE_URL'))
    def choose_api_key(self):
        """
        Chooses the appropriate API key based on the state of the inference.

        Returns:
            str: The API key to be used for the current inference.

        Raises:
            None
        """
        if self.isInferenceUP:
            return str(os.getenv('AGENTARTIFICIAL_API_KEY'))
        return str(os.getenv('OPENAI_API_KEY'))

    def choose_model(self, model="mixtral"):
        """
        Choose a model for the task.

        Parameters:
            model (str, optional): The name of the model to choose. Defaults to "mixtral".

        Returns:
            str: The chosen model name.
        """
        if self.isInferenceUP:
            return model or "mixtral"
        return model or "gpt-3.5-turbo"

    def spot_check(self):
        """
        Sends a GET request to the AgentArtificial API to perform a spot check.

        Returns:
            bool: True if the request is successful and the status code is 200, False otherwise.
        """
        result = requests.get(
            url=f"{os.getenv('AGENTARTIFICIAL_BASE_URL')}", 
            headers= {"Authorization": f"Bearer,{os.getenv('AGENTARTIFICIAL_API_KEY')}"},
             timeout=30)
        print(result)

        if result.status_code == 200:
            return True
        return False
