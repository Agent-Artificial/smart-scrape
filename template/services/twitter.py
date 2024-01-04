import requests
import os
import json
import asyncio
from datetime import datetime
from template.utils import call_openai, tweet_prompts
from template.protocol import TwitterPromptAnalysisResult
import bittensor as bt

BEARER_TOKEN = os.environ.get('TWITTER_BEARER_TOKEN')

twitter_api_query_example = {
    'query': '(from:twitterdev -is:retweet) OR #twitterdev',
    'tweet.fields': "",
    'user.fields': "id,name,username",
    # 'start_time' : 'YYYY-MM-DDTHH:mm:ssZ', #todo need filter start and end time if need from prompt
    # 'end_time': 'YYYY-MM-DDTHH:mm:ssZ',
    'max_results': '2',
    'media.fields': "",
    # 'since_id': "Returns results with a Tweet ID greater than (that is, more recent than) the specified ID. The ID specified is exclusive and responses will not include it.",
    # 'unit_id': "Returns results with a Tweet ID less than (that is, older than) the specified ID. The ID specified is exclusive and responses will not include it."s
}

query_examples = [
    'pepsi OR cola OR "coca cola"',
    '("Twitter API" OR #v2) -"recent search"',
    'thankunext #fanart OR @arianagrande',

    'to:twitterdev OR to:twitterapi -to:twitter',
    'from:TwitterDev url:"https://t.co"',
    'retweets_of:twitterdev OR retweets_of:twitterapi',
    'place_country:US OR place_country:MX OR place_country:CA',
    'data @twitterdev -is:retweet',
    '"mobile games" -is:nullcast',
    'from:twitterdev -has:hashtags',
    'from:twitterdev announcement has:links',
    '#meme has:images',
    ': #icebucketchallenge has:video_link',
    'recommend #paris has:geo -bakery',
    'recommend #paris lang:en',
    '(kittens OR puppies) has:media',
    '#nowplaying has:mentions',
    '#stonks has:cashtags',
    '#nowplaying is:verified',
    'place:"new york city" OR place:seattle OR place:fd70c22040963ac7',
    'conversation_id:1334987486343299072 (from:twitterdev OR from:twitterapi)',
    'context:domain_id.entity_id',
    'has:media',
    'has:links OR is:retweet',
    '"twitter data" has:mentions (has:media OR has:links)',
    '(grumpy cat) OR (#meme has:images)',
    'skiing -snow -day -noschool',
    '(happy OR happiness) place_country:GB -birthday -is:retweet',
    '(happy OR happiness) lang:en -birthday -is:retweet',
    '(happy OR happiness OR excited OR elated) lang:en -birthday -is:retweet -holidays',
    'has:geo (from:NWSNHC OR from:NHC_Atlantic OR from:NWSHouston OR from:NWSSanAntonio OR from:USGS_TexasRain OR from:USGS_TexasFlood OR from:JeffLindner1) -is:retweet'
]

bad_query_examples = [
    '(OpenAI OR GPT-3) (#OpenAI OR #ArtificialIntelligence)',
    '(horrible OR worst OR sucks OR bad OR disappointing) (place_country:US OR place_country:MX OR place_country:CA)'

]

def get_query_gen_prompt(prompt):
    content = f"""
        Given the specific User's prompt: '{prompt}', please perform the following tasks and provide the results in a JSON object format:

        1. Identify and list the key keywords which is related to User's prompt.
  ´      2. Determine and list relevant hashtags which is related to User's prompt.
        3. Identify and list any significant user mentions frequently associated with User's prompt, but don't create if users has not mentioned any user
        4. Generate Twitter API query params based on examples and your knowledge below, user keywords, mentions, hashtags for query which is related to User's Prompt.
        

        Twitter API Params: "{twitter_api_query_example}"
        Twitter API Params.query right work: "{query_examples}"
        Twitter API Params.query does not work: {bad_query_examples}
        Twitter API Params rules:
            - If a query.word consists of two or more words, enclose them in quotation marks, i.e "Coca cola"
            - Don't use "since:" and "until:" for date filter
            - end_time must be on or after start_date
            - media.fields allowed values: "duration_ms,height,media_key,preview_image_url,type,url,width"
            - max_results only between 10 - 100
            - user.fields only allowed: "created_at,description,entities,id,location,name,pinned_tweet_id,profile_image_url,protected,url,username,verified,withheld"
            - tweet.fields only allowed: "attachments,author_id,context_annotations,conversation_id,created_at,entities,geo,id,in_reply_to_user_id,lang,possibly_sensitive,referenced_tweets,reply_settings,source,text,withheld,edit_history_tweet_ids"

        Output example:
  ´      {{
            "keywords": ["list of identified keywords based on the prompt"],
            "hashtags": ["#relevantHashtag1", "..."],
            "user_mentions": ["@significantUser1", "..."],
            "api_params": {{
                "query": "constructed query based on keywords, hashtags, and user mentions",
                "tweet.fields": "all important fields needed to answer user's prompt",
                "user.fields": "relevant user fields",
                "max_results": "appropriate number based on user's prompt"
            }}
        }}"
    """
    return content

def get_fix_query_prompt(prompt, prompt_analysis, api_result):
    task = get_query_gen_prompt(prompt=prompt)
    content = F"""That was my task for you: "{task}",
    That was your result: {prompt_analysis}
    That was Twitter API's result: "{api_result}"
´
    Please, make a new better output to get better result from Twitter API.
    Output must be as Output example.
    """
    return content


class TwitterAPIClient:
    def __init__(self):
        # self.bearer_token = os.environ.get("BEARER_TOKEN")
        self.bearer_token = BEARER_TOKEN

    def bearer_oauth(self, r):
        """
        Method required by bearer token authentication.
        """
        r.headers["Authorization"] = f"Bearer {self.bearer_token}"
        r.headers["User-Agent"] = "v2RecentSearchPython"
        return r

    def connect_to_endpoint(self, url, params):
        response = requests.get(url, auth=self.bearer_oauth, params=params)
        print(response.status_code)
        if response.status_code != 200:
            raise Exception(response.status_code, response.text)
        return response.json()

    def get_recent_tweets(self, query_params):
        search_url = "https://api.twitter.com/2/tweets/search/recent"
        json_response = self.connect_to_endpoint(search_url, query_params)
        return json.dumps(json_response, indent=4, sort_keys=True)


    async def generate_query_params_from_prompt(self, prompt):
        """
        This function utilizes OpenAI's API to analyze the user's query and extract relevant information such as keywords, hashtags, and user mentions.
        """
        content  = get_query_gen_prompt(prompt)
        messages = [{'role': 'user', 'content': content }]
        bt.logging.info(content)
        res = await call_openai(messages, 0.2, "gpt-4-1106-preview", None,  {"type": "json_object"})
        response_dict = json.loads(res)
        bt.logging.info("generate_query_params_from_prompt Content: ", response_dict)
        return response_dict
    
    async def fix_twitter_query(self, prompt, query, api_result):
        """
        This method refines the user's initial query by leveraging OpenAI's API to parse and enhance the query with more precise keywords, hashtags, and user mentions, aiming to improve the search results from the Twitter API.
        """
        try:
            content  = get_fix_query_prompt(prompt=prompt,
                                                       prompt_analysis=query,
                                                       api_result=api_result)
            messages = [{'role': 'user', 'content': content }]
            bt.logging.info(content)
            res = await call_openai(messages, 0.2, "gpt-4-1106-preview", None,  {"type": "json_object"})
            response_dict = json.loads(res)
            bt.logging.info("generate_query_params_from_prompt Content: ", response_dict)
            return response_dict
        except Exception as e:
            bt.logging.info(e)
            return [], None
    
    # async def analyse_prompt_and_fetch_tweets(self, prompt):
    #     try:
    #         query = await self.generate_query_params_from_prompt(prompt)
    #         prompt_analysis = TwitterPromptAnalysisResult()
    #         prompt_analysis.fill(query)
    #         bt.logging.info(" ============================================= ")
    #         bt.logging.info(prompt_analysis.api_params['query'])
    #         prompt_analysis.api_params['max_results'] = 10
    #         bt.logging.info(" ============================================= ")
    #         result = self.get_recent_tweets(prompt_analysis.api_params)

    #         if len(result) < 1:
    #             new_query = await self.fix_twitter_query(prompt=prompt, query=query, api_result=result)
    #             prompt_analysis = TwitterPromptAnalysisResult()
    #             prompt_analysis.fill(new_query)
    #             prompt_analysis.api_params['max_results'] = 10
    #             result = self.get_recent_tweets(prompt_analysis.api_params)

    #         bt.logging.info("Tweeter fetched ===================================================")
    #         bt.logging.info(result)
    #         bt.logging.info("Tweeter fetched ===================================================")
    #         return result, prompt_analysis
    #     except Exception as e:
    #         if e.status == 401:
    #             bt.logging.info("Unauthorized access, check API credentials.")
    #         else:
    #             bt.logging.info(e)
    #             try:
    #                 new_query = await self.fix_twitter_query(prompt=prompt, query=query, api_result=e)
    #                 prompt_analysis = TwitterPromptAnalysisResult()
    #                 prompt_analysis.fill(new_query)
    #                 prompt_analysis.api_params['max_results'] = 10
    #                 result = self.get_recent_tweets(prompt_analysis.api_params)
    #                 return result, prompt_analysis
    #             except Exception as e:
    #                 return [], None
    #         return [], None
        
    async def analyze_twitter_query(self, prompt):
        try:
            query = await self.generate_query_params_from_prompt(prompt)
            prompt_analysis = TwitterPromptAnalysisResult()
            prompt_analysis.fill(query)
            return prompt_analysis
        except Exception as e:
            print(e)
            return {}
        
    async def analyse_prompt_and_fetch_tweets(self, prompt):
        try:
            query, prompt_analysis = await self.generate_and_analyze_query(prompt)
            result = self.get_recent_tweets(prompt_analysis.api_params)

            if not result:
                result, prompt_analysis = await self.retry_with_fixed_query(prompt, query)

            self.log_fetched_tweets(result)
            return result, prompt_analysis

        except Exception as e:
            return await self.handle_exceptions(e, prompt, query)

    async def generate_and_analyze_query(self, prompt):
        query = await self.generate_query_params_from_prompt(prompt)
        prompt_analysis = TwitterPromptAnalysisResult()
        prompt_analysis.fill(query)
        self.set_max_results(prompt_analysis.api_params)
        bt.logging.info("Tweets Query ===================================================")
        bt.logging.info(prompt_analysis)
        bt.logging.info("================================================================")
        return query, prompt_analysis

    def set_max_results(self, api_params, max_results=10):
        api_params['max_results'] = max_results

    async def retry_with_fixed_query(self, prompt, query):
        new_query = await self.fix_twitter_query(prompt=prompt, query=query, api_result={})
        prompt_analysis = TwitterPromptAnalysisResult()
        prompt_analysis.fill(new_query)
        self.set_max_results(prompt_analysis.api_params)
        result = self.get_recent_tweets(prompt_analysis.api_params)
        return result, prompt_analysis

    def log_fetched_tweets(self, result):
        bt.logging.info("Tweets fetched ===================================================")
        bt.logging.info(result)
        bt.logging.info("================================================================")

    async def handle_exceptions(self, e, prompt, query):
        if hasattr(e, 'status') and e.status == 401:
            bt.logging.info("Unauthorized access, check API credentials.")
        else:
            bt.logging.info(e)
            return await self.attempt_fix_and_fetch(prompt=prompt, query=query, error=e)
        return [], None

    async def attempt_fix_and_fetch(self, prompt, query, error):
        try:
            return await self.retry_with_fixed_query(prompt, query)
        except Exception as e:
            bt.logging.info(e)
            return [], None

# if __name__ == "__main__":
#     client = TwitterAPIClient()
#     query_params = {
#     #   'query': "(OpenAI OR GPT-3 OR DALL-E OR ChatGPT OR artificial intelligence OR machine learning OR #OpenAI OR #ArtificialIntelligence OR #MachineLearning OR #GPT3 OR #DALLE OR #ChatGPT OR #AITrends OR #TechTrends) -is:retweet"
#     #   'query': '(OpenAI OR GPT-3) (#OpenAI OR #ArtificialIntelligence)'
#     # 'query': '(x1 OR x3) (#x2 OR #x4) (x1 OR x3) (#x2 OR #x4)'
#         # 'tweet.fields': 'author_id'
#         # 'query': "#nowplaying (horrible OR worst OR sucks OR bad OR disappointing) (place_country:US OR place_country:MX OR place_country:CA) -happy -exciting -excited -favorite -fav -amazing -lovely -incredible"


#         'query': '(OpenAI OR GPT-3 OR DALL-E OR ChatGPT OR AI OR artificial intelligence OR machine learning OR technology OR trends) (#OpenAI OR #AI OR #ArtificialIntelligence OR #MachineLearning OR #GPT3 OR #DALLE OR #ChatGPT OR #TechTrends) -is:retweet since:2022-01-01T00:00:00Z until:2022-12-31T23:59:59Z'
#     }
#     # result = client.get_recent_tweets(query_params=query_params)
#     # print(result)

#     # # Run the async function using asyncio
#     for i in tweet_prompts:
#         result = asyncio.run(client.analyse_prompt_and_fetch_tweets(i))
        
#         print(len(result))
#         # if len(result) > 0
#         #    print(result)
    
#     # client.get_recent_tweets(query_params)