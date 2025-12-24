import asyncio
import os
import logging
import sys
from typing import Annotated, Any
from typing_extensions import TypedDict

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage, ToolMessage
from langchain_ollama import ChatOllama
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver

# --- LOGGING CONFIGURATION ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("agent_debug.log")
    ]
)
logger = logging.getLogger("WebNavigatorClient")

server_params = StdioServerParameters(
    command='uv',
    args=['run', 'mcp_server.py'],
    env=os.environ.copy(),
)

class AgentState(TypedDict):
    messages: Annotated[list, add_messages]

async def run_agent_loop(model_name="qwen2.5:14b"):
    logger.info(f"Starting session with model: {model_name}")
    
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            
            logger.info("Initializing MCP session...")
            await session.initialize()
            
            # Adapter to langchain
            mcp_tools_list = await session.list_tools()
            langchain_tools = []

            for mcp_tool in mcp_tools_list.tools:
                tool_name = mcp_tool.name
                tool_description = mcp_tool.description
                
                # Wrapper function
                async def dynamic_tool_func(*args, __name=tool_name, **kwargs):
                    # --- NEW ROBUST ARGUMENT EXTRACTION ---
                    # 1. Start with kwargs
                    actual_args = kwargs
                    
                    # 2. If LLM used positional args (args: [...])
                    if not actual_args and args:
                        # If the tool expects a single string (like visit_url)
                        # We try to wrap the first positional arg into a dict if needed
                        # But typically LangChain tools should use kwargs.
                        pass 

                    # 3. Handle specific Qwen/Ollama nesting patterns found in logs
                    if "kwargs" in actual_args and isinstance(actual_args["kwargs"], dict):
                        # Merge or replace with nested kwargs
                        inner_kwargs = actual_args.pop("kwargs")
                        actual_args.update(inner_kwargs)
                    
                    if "args" in actual_args and isinstance(actual_args["args"], (list, tuple)):
                        inner_args = actual_args.pop("args")
                        if inner_args:
                            # If it's visit_url(url="..."), the first arg is the URL
                            if __name == "visit_url" and "url" not in actual_args:
                                actual_args["url"] = inner_args[0]
                            # If it's click_by_text(text="..."), the first arg is the text
                            elif __name == "click_by_text" and "text" not in actual_args:
                                actual_args["text"] = inner_args[0]

                    # 4. Final safety check: if actual_args is empty but we have kwargs with data
                    # (This handles the case where Qwen sends {'url': '...'} directly)
                    # No changes needed, actual_args already has the data.

                    logger.info(f"MCP Tool Call: [{__name}] | Final Arguments: {actual_args}")
                    
                    try:
                        # Call the real MCP server
                        result = await session.call_tool(__name, arguments=actual_args)
                        output = result.content[0].text
                        logger.info(f"MCP Tool Response: [{__name}] | Length: {len(output)} chars")
                        return output
                    except Exception as e:
                        logger.error(f"Error executing {__name}: {str(e)}")
                        return f"Error executing tool: {str(e)}"
   
                # Assegniamo metadati per permettere all'LLM di capire cosa fa il tool
                dynamic_tool_func.__name__ = tool_name
                dynamic_tool_func.__doc__ = tool_description
                
                # Creiamo il tool strutturato per LangChain
                lc_tool = tool(dynamic_tool_func)
                langchain_tools.append(lc_tool)

            logger.info(f"Loaded {len(langchain_tools)} tools from MCP server.")

            # LLM setup
            llm = ChatOllama(model=model_name, temperature=0)
            llm_with_tools = llm.bind_tools(langchain_tools)

            # Graph definition
            async def chatbot(state: AgentState):
                """Node that decides what to do (speak or call tools)"""
                logger.info("Agent is thinking...")
                response = await llm_with_tools.ainvoke(state["messages"])
                return {"messages": [response]}

            async def tool_executor(state: AgentState):
                """Node that executes the tools"""
                outputs = []
                last_message = state["messages"][-1]
                
                logger.info(f"Agent requested {len(last_message.tool_calls)} tool(s).")
                
                for tool_call in last_message.tool_calls:
                    t_name = tool_call["name"]
                    t_args = tool_call["args"]
                    t_id = tool_call["id"]
                    
                    logger.info(f"Executing Tool: {t_name} | Raw Args: {t_args}")
                    
                    selected_tool = next(t for t in langchain_tools if t.name == t_name)
                    tool_result = await selected_tool.ainvoke(t_args)
                    
                    outputs.append(
                        ToolMessage(
                            content=str(tool_result),
                            name=t_name,
                            tool_call_id=t_id,
                        )
                    )
                return {"messages": outputs}

            # --- GRAPH CONSTRUCTION ---
            workflow = StateGraph(AgentState)
            workflow.add_node("agent", chatbot)
            workflow.add_node("tools", tool_executor)

            workflow.add_edge(START, "agent")
            
            # Conditional logic: If the LLM wants to call tools -> go to tools, otherwise -> END
            def should_continue(state: AgentState):
                last_message = state["messages"][-1]
                if last_message.tool_calls:
                    return "tools"
                return END

            workflow.add_conditional_edges("agent", should_continue, ["tools", END])
            workflow.add_edge("tools", "agent") # Loop back to agent after tool execution

            memory = MemorySaver()
            app = workflow.compile(checkpointer=memory)

            # --- EXECUTION ---
            config = {"configurable": {"thread_id": "Alberto-Web-Navigator-Session"}}
            
            print("\n" + "="*50)
            print("BROWSER AGENT SESSION STARTED")
            print("="*50)
            print("(type 'quit' or 'exit' to stop)")

            while True:
                try:
                    user_input = input("\nYou: ")
                    if user_input.lower() in ["quit", "exit"]:
                        break
                    
                    # Streaming graph execution
                    async for event in app.astream(
                        {"messages": [HumanMessage(content=user_input)]}, 
                        config=config,
                        stream_mode="updates"
                    ):
                        for node_name, values in event.items():
                            if node_name == "agent":
                                last_msg = values["messages"][-1]
                                if not last_msg.tool_calls:
                                    print(f"AI: {last_msg.content}")
                                else:
                                    logger.info("Agent decided to use tools. Moving to execution node.")
                            elif node_name == "tools":
                                logger.info("Tool execution node finished. Returning to agent.")

                except Exception as e:
                    logger.critical(f"Fatal error in chat loop: {e}", exc_info=True)

if __name__ == "__main__":
    model_name = "qwen2.5:14b"
    try:
        asyncio.run(run_agent_loop(model_name=model_name))
    except KeyboardInterrupt:
        logger.info("Interrupt received. Goodbye!")