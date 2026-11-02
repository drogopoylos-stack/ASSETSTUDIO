"""Verify workspace scoping and both Studio session integrations without starting an agent."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('ASSET_STUDIO_SECONDARY', '1')
from asset_studio.extra_mcp import servers


class ExtraMcpTests(unittest.TestCase):
    def test_scoping_and_disabled(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'extra.json'
            path.write_text(json.dumps({'mcpServers': {
                'photocraft': {'type': 'stdio', 'command': 'pc.exe', 'args': ['mcp', '${workspace}'], 'env': {'ROOT': '${workspace}'}},
                'studio': {'command': 'do-not-override'},
                'disabled': {'command': 'off', 'enabled': False},
            }}), encoding='utf-8')
            result = servers(folder, config_path=path)
            self.assertEqual(set(result), {'photocraft'})
            self.assertEqual(result['photocraft']['args'][1], str(Path(folder).resolve()))
            self.assertEqual(result['photocraft']['env']['ROOT'], str(Path(folder).resolve()))

    def test_both_clients_receive_server(self):
        from asset_studio import cc_session, codex_app
        extra = {'photocraft': {'type': 'stdio', 'command': 'pc.exe', 'args': ['mcp']}}
        with tempfile.TemporaryDirectory() as folder:
            with patch('asset_studio.extra_mcp.servers', side_effect=lambda cwd: json.loads(json.dumps(extra))), patch.object(cc_session, '_MCP_DIR', Path(folder)), patch.object(cc_session, '_mcp_on', return_value=True):
                config = json.loads(cc_session._mcp_config_file('test', folder).read_text())
                self.assertEqual(config['mcpServers']['photocraft']['command'], 'pc.exe')
                self.assertIn('studio', config['mcpServers'])
                with patch.object(codex_app, '_developer_notes', return_value=''), patch.object(codex_app, '_hook_config', return_value={}), patch.object(codex_app, '_mcp_config', return_value={'command': 'python', 'args': ['mcp_engine.py'], 'env': {}}):
                    config = codex_app._thread_params(folder, 'full', 'default')['config']['mcp_servers']
                    self.assertEqual(config['photocraft']['command'], 'pc.exe')
                    self.assertNotIn('type', config['photocraft'])
                    self.assertIn('studio', config)

    def test_extra_server_available_when_studio_tools_are_off(self):
        from asset_studio import cc_session, codex_app
        extra = {'photocraft': {'type': 'stdio', 'command': 'pc.exe', 'args': ['mcp']}}
        with tempfile.TemporaryDirectory() as folder:
            with patch('asset_studio.extra_mcp.servers', side_effect=lambda cwd: json.loads(json.dumps(extra))), patch.object(cc_session, '_MCP_DIR', Path(folder)), patch.object(cc_session, '_mcp_on', return_value=False):
                args = cc_session._claude_stream_args('claude', None, 'default', 'default', False, 'default', project_id='extra-test', cwd=folder)
                config = json.loads(Path(args[args.index('--mcp-config') + 1]).read_text())
                self.assertEqual(set(config['mcpServers']), {'photocraft'})
                with patch.object(codex_app, '_developer_notes', return_value=''), patch.object(codex_app, '_hook_config', return_value={}):
                    config = codex_app._thread_params(folder, 'full', 'default')['config']['mcp_servers']
                    self.assertEqual(set(config), {'photocraft'})


if __name__ == '__main__':
    unittest.main()
