"""Deterministic observations; counts are evidence, not a diagnosis."""
from collections import Counter
import json


def trace_signals(text):
    try:
        messages = json.loads(text)
        if not isinstance(messages, list):
            raise ValueError('Expected a conversation')
        assistants = [m for m in messages if m.get('role') == 'assistant']
        commands = []
        for message in assistants:
            for call in message.get('tool_calls') or []:
                if call['function']['name'] == 'bash':
                    args = json.loads(call['function']['arguments'])
                    if isinstance(args.get('command'), str):
                        commands.append(args['command'])
        counts = Counter(commands)
        return {'complete_trace': True, 'assistant_messages': len(assistants),
                'empty_no_tool_responses': sum(not m.get('tool_calls') and not (m.get('content') or '').strip() for m in assistants),
                'bash_calls': len(commands), 'repeated_bash_calls': sum(n - 1 for n in counts.values()),
                'most_repeated_commands': [{'command': command[:300], 'count': count}
                    for command, count in counts.most_common(5) if count > 1]}
    except (ValueError, KeyError, TypeError, AttributeError):
        return {'complete_trace': False, 'note': 'Trace missing, malformed or truncated; no counts inferred'}
