"""Offline sandbox contract driver; never used for scored agent execution.

Exercise bash followed by completion. Support both plain-text completion and the
explicit finish tool convention. This is a smoke check, not a generic tool agent.
"""
import json
import runpy
import sys
import types


def check(path):
    runtime = runpy.run_path(path)

    class FixtureAPI(runtime['FixtureAPI']):
        def model(self, messages, tools):
            response = super().model(messages, tools)
            if self.calls > 1 and any(t['function']['name'] == 'finish' for t in tools):
                return {'role': 'assistant', 'content': None, 'tool_calls': [{
                    'id': f'contract-finish-{self.calls}', 'type': 'function', 'function': {
                        'name': 'finish', 'arguments': json.dumps({
                            'summary': 'The fixture command succeeded.',
                            'verification_command': 'printf contract-check',
                            'verification_output_excerpt': 'contract-check'})}}]}
            return response

    if 'load_policy' in runtime:
        namespace = runtime['load_policy']()
    else:
        module = types.ModuleType('agent_policy')
        sys.modules[module.__name__] = module
        exec(compile(runtime['POLICY_SOURCE'], '<agent_policy>', 'exec'), module.__dict__)
        namespace = module.__dict__
    fixture = FixtureAPI()
    namespace['run_agent'](fixture, 'Run a harmless inspection command and then finish.')
    if fixture.calls < 2 or fixture.bash_calls < 1 or not fixture.outputs:
        raise ValueError('Agent did not complete the model/tool/result contract')
    print(json.dumps({'contract': 'passed', 'model_calls': fixture.calls}))


if __name__ == '__main__':
    check(sys.argv[1])
