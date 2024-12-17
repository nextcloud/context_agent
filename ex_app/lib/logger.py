import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', handlers=[logging.StreamHandler()])
logger = logging.getLogger('context_agent')
logger.setLevel(logging.INFO)

def log(nc, level, content):
	logger.log((level+1)*10, content)
	try:
		nc.log(level, content)
	except:
		pass