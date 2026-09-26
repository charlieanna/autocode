"""Selected implementation models survive the existing conversation handoff."""
import unittest
from test_chat_bridge import ChatFixture


class BuilderConversationTests(ChatFixture, unittest.TestCase):
    def test_every_selected_role_is_preserved_through_chat_restart_and_attachment(self):
        models = {'glm_model':'openai/gpt-5.6-sol','astra_model':'zai-coding-plan/glm-5.3',
                  'terra_model':'openai/gpt-5.6-terra','sol_model':'openai/gpt-6-astra'}
        self.console.catalogue.fetch = lambda **kwargs: {'usable':True,'models':list(models.values())}
        doc = self.create_conversation(models=models)
        self.assertEqual('openai/gpt-5.6-sol', self.provider_calls[0][1])
        self.assertEqual([], self.commands())
        self.console = self.make_console()
        self.console.catalogue.fetch = lambda **kwargs: {'usable':True,'models':list(models.values())}
        restored = self.console.conversation_get(doc['id'])
        self.assertEqual(models, restored['models'])
        self.assertEqual(doc['messages'], restored['messages'])
        self.console.conversation_attach({'id':doc['id'],'project':str(self.workspace)})
        self.eventually(lambda: self.console.conversation_get(doc['id'])['attachment']['status']=='linked')
        self.settled()
        self.assertEqual(1, len(self.commands()))
        args = self.commands()[0]
        for role, model in models.items():
            self.assertEqual(model, args[args.index('--'+role.replace('_','-'))+1])
        self.assertIn('--joint-planning', args)

    def test_openai_terra_choice_survives_restart_and_project_attachment(self):
        self.console.catalogue.fetch = lambda **kwargs: {'usable':True,'models':['openai/gpt-5.6-terra','zai-coding-plan/glm-5.3']}
        doc = self.create_conversation(models={'terra_model': 'openai/gpt-5.6-terra'})
        self.assertEqual([], self.commands())
        self.assertEqual('zai-coding-plan/glm-5.3', self.provider_calls[0][1])
        self.console = self.make_console()
        self.console.catalogue.fetch = lambda **kwargs: {'usable':True,'models':['openai/gpt-5.6-terra','zai-coding-plan/glm-5.3']}
        restored = self.console.conversation_get(doc['id'])
        self.assertEqual({'glm_model': 'zai-coding-plan/glm-5.3', 'terra_model': 'openai/gpt-5.6-terra'}, restored['models'])
        self.assertEqual(doc['messages'], restored['messages'])
        self.console.conversation_attach({'id': doc['id'], 'project': str(self.workspace)})
        self.eventually(lambda: self.console.conversation_get(doc['id'])['attachment']['status'] == 'linked')
        self.settled()
        commands = self.commands()
        self.assertEqual(1, len(commands))
        self.assertIn('--joint-planning', commands[0])
        self.assertEqual('openai/gpt-5.6-terra', commands[0][commands[0].index('--terra-model') + 1])
        self.assertNotIn('--terra-provider', commands[0])


if __name__ == '__main__':
    unittest.main()
