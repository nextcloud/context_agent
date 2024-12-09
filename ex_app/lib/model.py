
import os

from langchain_openai import ChatOpenAI
from langchain.callbacks.tracers import ConsoleCallbackHandler


# OpenAI
os.environ["OPENAI_API_KEY"] = ''

# Groq
#os.environ["OPENAI_API_KEY"] = ''

#"""
model = ChatOpenAI(
	model="gpt-4o",
	#model="llama-3.1-70b-versatile",
	temperature=0,
	#base_url="https://api.groq.com/openai/v1",
	#callbacks=[ConsoleCallbackHandler()],
)
#"""

"""
from local_model import ChatWithNextcloud
# Instantiate chat model
model = ChatWithNextcloud()

"""