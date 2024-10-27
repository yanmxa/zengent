from typing import Union, Tuple, List
import os
import sys
import json
import rich
from pydantic import ValidationError
import datetime
from rich.prompt import Prompt
from rich.syntax import Syntax
import importlib
from openai.types.chat import (
    ChatCompletionMessage,
    ChatCompletionMessageParam,
    ChatCompletionToolParam,
    ChatCompletionSystemMessageParam,
    ChatCompletionUserMessageParam,
    ChatCompletionToolMessageParam,
    ChatCompletionAssistantMessageParam,
)
import rich.rule
from type import ChatMessage, StatusCode
import re
import json

from tool import (
    Permission,
    chat_tool,
    tool_name,
    func_metadata,
)


current_dir = os.path.dirname(os.path.realpath(__file__))
FINAL_ANSWER = "ANSWER:"


class Agent:

    def __init__(
        self,
        client,
        name,
        system,
        tools=[],
        debug=False,
        permission=Permission.ALWAYS,
        structured_output=True,  # indicate whether the output is an structured format
    ):
        self.client = client
        self.name = name
        self.system = system
        self.tools = tools
        self.permission = permission
        self.description = ""
        self.structured_output = structured_output

        self._max_iter = 6

        # register the external func(modules) to the agent
        self._func_modules = {}
        for tool in tools:
            func_name, func_module = tool_name(tool)
            self._func_modules[func_name] = func_module

        # tools for the LLM
        self._tools: List[ChatCompletionToolParam] = []
        if self.structured_output:
            # 1. append tools to _system
            self._system = self._build_system("structured_agent.md")
            self._system += self._tool_markdown(tools)
        else:
            # 1. generate the _tools from tools: https://platform.openai.com/docs/guides/function-calling
            # 2. render the basic_agent to _system(time, name, system, FINAL_ANSWER)
            self._tools = self._chat_completion_tools(tools)
            self._system = self._build_system("basic_agent.md")

        self.messages: List[ChatCompletionMessageParam] = [
            ChatCompletionSystemMessageParam(
                role="system",
                content=self._system,
            )
        ]

    def run(self, message: Union[ChatCompletionMessageParam, str]):
        if isinstance(message, str):
            message = ChatCompletionUserMessageParam(content=message, role="user")
        self.messages.append(message)

        status_code, result = self._run()
        i = 0
        while i < self._max_iter:
            if status_code == StatusCode.ANSWER:
                rich.get_console().print(f"✨ {result} \n", style="bold green")
                user_input = (
                    Prompt.ask("🧘 [dim][red]Exit[/red] to quit[/dim]").strip().lower()
                )
                rich.print()

                if user_input in {"exit", "e"}:
                    rich.get_console().print("👋 [blue]Goodbye![/blue]\n")
                    break
                else:
                    self.messages.append(
                        ChatCompletionUserMessageParam(content=user_input, role="user"),
                    )
                    i = 0  # Reset iteration count for new input
            elif status_code == StatusCode.THOUGHT:
                rich.print()  # TODO: add the thought, might be need add user prompt
            elif status_code == StatusCode.OBSERVATION:
                rich.get_console().print(f"{result}\n", style="italic dim")
            else:  # StatusCode.ERROR, StatusCode.NONE, StatusCode.ACTION_FORBIDDEN
                rich.get_console().print(f"{result}", style="red")
                return
            rich.get_console().rule("🤖", characters="~", style="dim")
            status_code, result = self._run()
            i += 1
        if i == self._max_iter:
            rich.get_console().print(
                f"💣 [red]Reached maximum of {self._max_iter} iterations![/red]\n"
            )

    # https://github.com/openai/openai-python/blob/main/src/openai/types/chat/chat_completion_message_param.py
    # https://github.com/openai/openai-python/blob/main/src/openai/types/chat/chat_completion_tool_param.py
    # https://platform.openai.com/docs/guides/function-calling
    def _run(self) -> Tuple[StatusCode, str]:
        chat_message: ChatCompletionMessage = self.client(self.messages, self._tools)
        # append the assistant request
        self.messages.append(chat_message)

        # start structured content process
        if self.structured_output:
            return self._structured_content(chat_message.content)
        # end the structured content process

        if chat_message.tool_calls:
            tool = chat_message.tool_calls[0]
            func_name = tool.function.name
            func_args = tool.function.arguments
            if isinstance(func_args, str):
                func_args = json.loads(tool.function.arguments)
            func_tool_call_id = tool.id

            # validate the tool
            if not func_name in self._func_modules:
                return (
                    StatusCode.ERROR,
                    f"The function [yellow]{func_name}[/yellow] isn't registered!",
                )

            # call tool: not all -> exit
            if not self._allow_action(func_name, func_args):
                return StatusCode.ACTION_FORBIDDEN, "🚫 Action cancelled by the user."

            # invoke function
            func_module = self._func_modules[func_name]
            if func_module not in globals():
                globals()[func_module] = importlib.import_module(func_module)
            func_tool = getattr(sys.modules[func_module], func_name)
            observation = func_tool(**func_args)

            # append the tool response: observation
            self.messages.append(
                ChatCompletionToolMessageParam(
                    tool_call_id=func_tool_call_id,
                    content=f"{observation}",
                    role="tool",
                )
            )
            return StatusCode.OBSERVATION, observation
        elif chat_message.content:
            if chat_message.content.startswith(FINAL_ANSWER):
                return (
                    StatusCode.ANSWER,
                    chat_message.content.removeprefix(FINAL_ANSWER),
                )
            else:
                return (StatusCode.THOUGHT, chat_message.content)
        else:
            return (
                StatusCode.NONE,
                f"Invalid response message: {chat_message}",
            )

    def _structured_content(self, content: str) -> Tuple[StatusCode, str]:
        try:
            decoder = json.JSONDecoder()
            json_content, _ = decoder.raw_decode(content.strip())

            chat_message = ChatMessage.model_validate(json_content)

            if chat_message.thought:
                rich.get_console().print()
                rich.get_console().print("\n".join(chat_message.thought))
                rich.get_console().print()
            if chat_message.action:
                func_name = chat_message.action.name
                func_args = chat_message.action.args
                func_edit = chat_message.action.edit
                # validate the tool
                if not func_name in self._func_modules:
                    return (
                        StatusCode.ERROR,
                        f"The function [yellow]{func_name}[/yellow] isn't registered!",
                    )

                # validate the permission
                if not self._allow_action(func_name, func_args, func_edit=func_edit):
                    return (
                        StatusCode.ACTION_FORBIDDEN,
                        "🚫 Action cancelled by the user.",
                    )

                # observation
                module_name = self._func_modules[func_name]
                if module_name not in globals():
                    globals()[module_name] = importlib.import_module(module_name)
                func = getattr(sys.modules[module_name], func_name)
                observation = func(**func_args)
                self.messages.append(
                    ChatCompletionUserMessageParam(
                        role="user", content=f"{observation}"
                    )
                )
                return StatusCode.OBSERVATION, f"{observation}"
            elif chat_message.answer:
                return StatusCode.ANSWER, chat_message.answer
            elif chat_message.thought:
                return StatusCode.THOUGHT, "\n".join(chat_message.thought)
            else:
                return (
                    StatusCode.NONE,
                    f"can't parse validate action, thought, or answer from the response",
                )

        except ValidationError as e:
            self.messages.append(
                ChatCompletionUserMessageParam(
                    content=f"Validate error in the response: {e}",
                    role="user",
                ),
            )
            return (
                StatusCode.OBSERVATION,
                f"{content}\n Validate error, Should only contain the JSON object:\n {e}",
            )
        except Exception as e:
            return StatusCode.ERROR, f"{content}\n An structured error occurred: {e}"

    def _tool_markdown(self, tools) -> str:
        system_tool_content = ["## Available Tools:\n"]
        for tool in tools:
            func_name, func_args, func_desc = func_metadata(tool)
            tool_md = f"### {func_name}\n"
            tool_md += f"**Parameters**: {', '.join(func_args)}\n\n"
            tool_md += f"**Description**: {func_desc}\n"
            system_tool_content.append(tool_md)
        if len(tools) == 0:
            system_tool_content.append("### No tools are available")
        return "\n".join(system_tool_content)

    def _build_system(self, file_name) -> str:
        with open(os.path.join(current_dir, "..", "prompt", file_name), "r") as f:
            agent_system = f.read()

        agent_system = agent_system.replace(
            "{{time}}", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )
        agent_system = agent_system.replace("{{name}}", self.name)
        agent_system = agent_system.replace("{{system}}", self.system)
        agent_system = agent_system.replace("{{FINAL_ANSWER}}", FINAL_ANSWER)  # basic
        return agent_system

    def _chat_completion_tools(self, tools):
        func_tools = []
        for tool in tools:
            # https://github.com/openai/openai-python/blob/main/src/openai/types/chat/completion_create_params.py#L251
            _, _, chat_completion_tool = chat_tool(tool)
            func_tools.append(chat_completion_tool)
        return func_tools

    def _allow_action(self, func_name, func_args, func_edit=0):
        tool_info = f"🛠  [yellow]{func_name}[/yellow] - {func_args}"
        if func_name == "execute_code":
            rich.get_console().print(
                Syntax(
                    func_args["code"],
                    func_args["language"],
                    theme="monokai",
                    line_numbers=True,
                )
            )
            tool_info = f"🛠  [yellow]{func_args['language']}[/yellow]"

        if self.permission == Permission.NONE:
            rich.get_console().print(tool_info)
            return True

        if self.permission == Permission.AUTO and func_edit == 0:  # enable auto
            rich.get_console().print(tool_info)
            return True

        while True:
            proceed = (
                rich.get_console()
                .input(f"{tool_info}  👉 [dim]Y/N: [/dim]")
                .strip()
                .upper()
            )
            if proceed == "Y":
                rich.print()
                return True
            elif proceed == "N":
                return False
            else:
                rich.get_console().print(
                    "⚠️ Invalid input! Please enter 'Y' or 'N'.\n", style="yellow"
                )
