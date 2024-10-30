import streamlit as st
from dotenv import load_dotenv
import os
from typing import Annotated
from langchain_anthropic import ChatAnthropic
from langchain_community.tools.tavily_search import TavilySearchResults
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from typing_extensions import TypedDict
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from langchain.agents import AgentType, initialize_agent, load_tools

# Load environment variables
load_dotenv()

# Initialize session state if not already done
if 'graph' not in st.session_state:
    memory = MemorySaver()

    class State(TypedDict):
        messages: Annotated[list, add_messages]
        is_cover_letter_created: bool
        user_query: str

    # Initialize LangChain components
    llm = ChatAnthropic(model="claude-3-5-sonnet-20240620")
    tools = load_tools(["google-jobs"], llm=llm)
    llm_with_tools = llm.bind_tools(tools)

    # Define graph nodes
    def chatbot(state: State):
        print("--- CHATBOT CALLED ---")
        if state["is_cover_letter_created"] == True:
            return state
        response = llm_with_tools.invoke(state["messages"])

        print("Chatbot response: ", response)
        return {"messages": [response]}

    def cover_letter_writer(state: State):
        job_details = state["messages"][-1].content
        
        # Get user's background
        user_background = ""
        for message in state["messages"]:
            if isinstance(message, HumanMessage):
                user_background = message.content
                break
        
        cover_letter_prompt = """
        You are an expert cover letter writer. Using the job details provided and the user's background:
        1. Write a compelling, personalized cover letter
        2. Focus on relevant skills and experiences
        3. Keep it concise (max 300 words)
        4. Use a professional but engaging tone
        5. Follow standard cover letter format
        6. Highlight specific aspects of the job that match the candidate's background

        Format the letter properly with date, addresses, and proper salutation.
        ALWAYS generate a cover letter for a job you have been given, even if you feel there is a mismatch between the job requirements and experience.
        """
        
        cover_letter_messages = [
            SystemMessage(content=cover_letter_prompt),
            HumanMessage(content=f"Please write a cover letter for this job: '''{job_details}'''.\n\n User background: '''{user_background}'''")
        ]
        
        response = llm.invoke(cover_letter_messages)
        state["is_cover_letter_created"] = True
        state["messages"].append(response)
        return state

    def decision_maker(state):
        if state["is_cover_letter_created"] == True:
            return END
        if state['messages'][-1].tool_calls:
            return "tools"
        else:
            return "cover_letter_writer"

    # Build graph
    graph_builder = StateGraph(State)
    graph_builder.add_node("chatbot", chatbot)
    tool_node = ToolNode(tools=tools)
    graph_builder.add_node("tools", tool_node)
    graph_builder.add_node("cover_letter_writer", cover_letter_writer)

    graph_builder.add_conditional_edges(
        "chatbot",
        decision_maker,
    )

    graph_builder.add_edge("tools", "chatbot")
    graph_builder.add_edge("cover_letter_writer", "chatbot")
    graph_builder.set_entry_point("chatbot")

    st.session_state.graph = graph_builder.compile(
        checkpointer=memory,
        interrupt_after=["tools"]
    )

    st.session_state.config = {"configurable": {"thread_id": "2"}}
    st.session_state.system_msg = '''
    If the latest message you receive is a user query asking for a job in any field, you will help the user find a job in the area they want using the google jobs search tool. 

    If the latest message you receive is the user's feedback on whether or not the job is okay, you will either make another tool call to google jobs, or if it is okay, then you will wait for the cover_letter_writer to draft a cover letter for the job (do not make a tool call in this case, it will run automatically).
    If the user wants another job, always just make a tool call to google jobs, do not ask more questions. 
    '''

# Streamlit UI
st.title("Job Search & Cover Letter Generator")

# Initial user input form
if 'job_search_started' not in st.session_state:
    st.session_state.job_search_started = False

if not st.session_state.job_search_started:
    with st.form("user_input_form"):
        user_input = st.text_area("Please enter your job search request and background:", 
                                 placeholder="Example: Give me a job in frontend software development. I have 3 years of experience.")
        submit_button = st.form_submit_button("Start Job Search")
        
        if submit_button and user_input:
            st.session_state.job_search_started = True
            events = st.session_state.graph.stream(
                {"messages": [
                    ("system", st.session_state.system_msg),
                    ("user", user_input)
                    ],
                    "is_cover_letter_created": False,
                    "user_query": user_input
                }, 
                st.session_state.config, 
                stream_mode="values"
            )
            
            # Store the job posting
            for event in events:
                    print(event)
            if 'messages' in event:
                    st.session_state.current_job = event["messages"][-1].content
                    st.rerun()

# Job feedback section
if st.session_state.job_search_started and 'current_job' in st.session_state:
    st.write("### Current Job Posting")
    st.write(st.session_state.current_job)
    
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Yes, I like this job"):
            # Process positive feedback
            response_content = "Yes"
            new_messages = [response_content]
            st.session_state.graph.update_state(
                st.session_state.config,
                {"messages": new_messages},
            )
            
            events = st.session_state.graph.stream(None, st.session_state.config, stream_mode="values")
            for event in events:
                 print(event)
            if 'messages' in event:
                    st.session_state.cover_letter = event["messages"][-1].content
                    st.session_state.job_search_started = False
                    st.rerun()
    
    with col2:
        if st.button("No, show me another job"):
            # Process negative feedback
            response_content = "No"
            new_messages = [response_content]
            st.session_state.graph.update_state(
                st.session_state.config,
                {"messages": new_messages},
            )
            
            events = st.session_state.graph.stream(None, st.session_state.config, stream_mode="values")
            for event in events:
                print(event)
            if 'messages' in event:
                    st.session_state.current_job = event["messages"][-1].content
                    st.rerun()

# Display cover letter if generated
if 'cover_letter' in st.session_state:
    st.write("### Your Cover Letter")
    st.write(st.session_state.cover_letter)
    
    if st.button("Start New Job Search"):
        for key in ['job_search_started', 'current_job', 'cover_letter']:
            if key in st.session_state:
                del st.session_state[key]
        st.rerun()