from typing import Union, Tuple, List
import os
from tool import build_from_template
from .chat_agent import Agent

current_dir = os.path.dirname(os.path.realpath(__file__))


FINAL_ANSWER = "ANSWER:"


class DefaultAgent(Agent):

    def __init__(
        self,
        client,
        name,
        description,
        tools=[],
        standalone=True,
    ):
        system = build_from_template(
            os.path.join(current_dir, "..", "prompt", "default_agent.md"),
            {
                "{{name}}": name,
                "{{system}}": description,
                "{{FINAL_ANSWER}}": FINAL_ANSWER,
            },
        )
        super().__init__(
            name,
            system,
            tools,
            client,
            standalone,
        )
