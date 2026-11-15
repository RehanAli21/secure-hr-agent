# A fake AI model for the tests.
#
# A real model is slow and may answer differently each time. The fake one follows a script:
# we tell it what to answer, one reply per call. It also remembers everything it was sent,
# so a test can check what the AI would have seen (for example: never the token).

from llama_index.core.base.llms.types import ChatMessage, MessageRole, ToolCallBlock
from llama_index.core.tools import FunctionTool


def text_reply(text: str) -> ChatMessage:
    """The model answers the user with text."""
    return ChatMessage(role=MessageRole.ASSISTANT, content=text)


def tool_call_reply(tool_name: str, args: dict[str, object] | None = None) -> ChatMessage:
    """The model asks to call a tool."""
    call = ToolCallBlock(tool_name=tool_name, tool_kwargs=args or {})
    return ChatMessage(role=MessageRole.ASSISTANT, blocks=[call])


class FakeLLM:
    def __init__(self) -> None:
        self.script: list[ChatMessage] = [text_reply("Hello!")]
        self.seen_messages: list[list[ChatMessage]] = []  # what it was sent, call by call
        self.seen_tools: list[FunctionTool] = []

    def ask(self, messages: list[ChatMessage], tools: list[FunctionTool]) -> ChatMessage:
        self.seen_messages.append(list(messages))  # a copy: the agent keeps adding to the list
        self.seen_tools = tools
        if len(self.script) > 1:
            return self.script.pop(0)
        return self.script[0]  # the last reply repeats forever

    def everything_seen(self) -> str:
        """All the text the model was given: every message and every tool description."""
        texts = [repr(message) for messages in self.seen_messages for message in messages]
        texts += [repr(tool.metadata) for tool in self.seen_tools]
        return "\n".join(texts)

    def last_tool_result(self) -> str:
        """The content of the last tool result the model was given."""
        tool_messages = [m for m in self.seen_messages[-1] if m.role == MessageRole.TOOL]
        return tool_messages[-1].content or ""
