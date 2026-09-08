"""Editable agent behavior, executed only inside the sandbox.

The optimizer may change this module's prompt, tools, helpers, context management,
and run_agent loop. The runtime supplies model calls, bash execution and logging.
"""
import json

AGENT_INSTRUCTION = '__SYSTEM_PROMPT__'
TOOLS = [{'type': 'function', 'function': {
    'name': 'bash', 'description': 'Execute a bash command in the container. Returns stdout and stderr.',
    'parameters': {'type': 'object', 'properties': {'command': {'type': 'string', 'description': 'The bash command to execute.'}}, 'required': ['command']},
}}]


def run_agent(api, instruction):
    messages = [{'role': 'system', 'content': AGENT_INSTRUCTION},
                {'role': 'user', 'content': 'Task:\n' + instruction}]
    while True:
        message = api.model(messages, TOOLS)
        messages.append(message)
        calls = message.get('tool_calls') or []
        if not calls:
            return
        for call in calls:
            try:
                args = json.loads(call['function']['arguments'])
                if call['function']['name'] != 'bash' or not isinstance(args.get('command'), str):
                    raise ValueError('Invalid bash tool call')
                result = api.bash(args['command'])
            except (ValueError, TypeError) as exc:
                result = str(exc)
            result = str(result)
            api.tool_result(call['id'], result)
            messages.append({'role': 'tool', 'tool_call_id': call['id'], 'content': result})
