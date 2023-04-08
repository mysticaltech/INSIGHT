import os
import time

from colorama import Fore
from collections import deque
from Bio import Entrez

import pinecone
from llama_index import GPTSimpleVectorIndex

from agents import boss_agent, worker_agent, data_cleaning_agent
from utils import execute_python, get_ada_embedding, get_relevant, insert_doc_llama_index, insert_doc_pinecone, query_knowledge_base

Entrez.email = os.environ['EMAIL']

PINECONE_API_KEY = os.environ['PINECONE_API_KEY']
PINECONE_ENV = os.environ['PINECONE_ENV']

MAX_TOKENS = 4097
OBJECTIVE = "Cure breast cancer"


# Configure Pinecone
pinecone.init(api_key=PINECONE_API_KEY, environment=PINECONE_ENV)

# Create Pinecone index
table_name = "insight-1"
dimension = 1536
metric = "cosine"
pod_type = "p1"

if table_name not in pinecone.list_indexes():
    pinecone.create_index(table_name, dimension=dimension, metric=metric, pod_type=pod_type)

# Connect to the index
pinecone_index = pinecone.Index(table_name)

# Create llama index
llama_index = GPTSimpleVectorIndex([])

task_id_counter = 1
task_list = deque()

tool_description = """
1) Query mygene API. This is useful for finding information on genes that are associated with diseases. If you wish to make a task to create an API request to mygene then simply say 'MYGENE:' followed by what you would like to search for. Example: 'MYGENE: look up information on genes that are linked to cancer'
2) Query PubMed API. This is useful for searching biomedical literature citations and abstracts. If you wish to make a task to create an API request to the PubMed API then simply say 'PUBMED:' followed by what you would like to search for.
""".strip()

#"Query PubChem API. This is useful for finding chemical information. Search chemicals by name, molecular formula, structure, and other identifiers. Find chemical and physical properties, biological activities, safety and toxicity information, patents, literature citations and more. If you wish to make a task to create an API request to PubChem then simply say 'PubChem:' followed by what you would like to search for. Example: 'PubChem: look up information on genes that are linked to cancer'
completed_tasks = []

print(Fore.CYAN, '\n*****OBJECTIVE*****\n')
print(OBJECTIVE)

while True:
    if task_id_counter > 1:
        executive_summary = query_knowledge_base(llama_index)
    else:
        executive_summary = "No tasks completed yet."

    task_list, thoughts = boss_agent(
        objective=OBJECTIVE,
        tool_description=tool_description,
        task_list=task_list,
        executive_summary=executive_summary,
        completed_tasks=completed_tasks
        )
    
    print(Fore.RED + "\n*****EXECUTIVE SUMMARY*****\n")
    print(Fore.RED + executive_summary)
    
    print(Fore.RED + "\n*****BOSS THOUGHTS*****\n")
    print(Fore.RED + thoughts)

    if task_list:

        print(Fore.WHITE + "\n*****TASK LIST*****\n")

        for t in task_list:
            print(t)

        task = task_list.popleft()

        context = ""
        if task_id_counter > 1:
            context = get_relevant(task, pinecone_index, num_relevant=3)

        print(Fore.RED + "\n*****NEXT TASK*****\n")
        print("task id: ", task_id_counter, 'task: ', task)

        result, python = worker_agent(OBJECTIVE, task, context)
        completed_tasks.append(task)
        print(Fore.GREEN + '\n*****TASK RESULT*****\n')
        print(Fore.GREEN + result)

        if python:
            result = execute_python(result)

        #if 'MYGENE' in task:
        # potential post processing

        if 'PUBMED' in task:
            # Text is often too large so we break it up
            cleaned_results = []
            for article in result:
                cleaned_results.append(data_cleaning_agent(article, OBJECTIVE))
            
            cleaned_result = '\n\n'.join(cleaned_results)
        else:
            cleaned_result = data_cleaning_agent(result, OBJECTIVE)

        print(Fore.BLUE + '\n*****CLEANED RESULT*****\n')
        print(Fore.BLUE + cleaned_result)

        # Store data
        vectorized_data = get_ada_embedding(cleaned_result)
        
        # Insert result into pinecone
        task_id = f"doc_id_{task_id_counter}"
        metadata = {"Task": task, "Result": cleaned_result}
        insert_doc_pinecone(pinecone_index, vectorized_data, task_id, metadata)

        # Insert result into llama_index
        insert_doc_llama_index(llama_index, cleaned_result, task_id)

        # TODO
        # Results that we store in pinecone every iteration might have considerable overlap.
        # Since the context window size of the LLMs is our main constraint we should fix this.
        #
        # Proposed solution:
        # Every few iterations run a job that finds semantically similar vectors, efficiently combines their metadata via an llm,
        # re-vectorizes the combined data and upserts it to pinecone with the combined metadata.
        # We should also remove the old vectors


        task_id_counter += 1

        time.sleep(3)

    if task_id_counter > 15:
        break