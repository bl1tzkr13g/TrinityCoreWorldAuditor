import unittest
from training_audit import assess

Q={'RequiredNpcOrGo1':44175,'RequiredNpcOrGoCount1':1,'RequiredSpell':774}
N={'AIName':'','ScriptName':''}
ROW={'source_type':0,'entryorguid':47057,'event_type':8,'event_param1':774,
     'event_param2':0,'event_phase_mask':0,'event_chance':100,'event_flags':0,
     'action_type':33,'action_param1':44175,'target_type':7}

class TrainingTests(unittest.TestCase):
    def test_observed_missing_handler(self):
        self.assertEqual('missing_handler',assess(Q,N,[],True)['status'])
    def test_repaired_handler(self):
        r=assess(Q,dict(N,AIName='SmartAI'),[ROW],True)
        self.assertEqual('handler_present',r['status']);self.assertFalse(r['automatic_apply'])
    def test_wrong_spell_or_credit_not_accepted(self):
        for field,value in [('event_param1',5185),('action_param1',47057),('target_type',1),('event_chance',50)]:
            self.assertEqual('review',assess(Q,dict(N,AIName='SmartAI'),[dict(ROW,**{field:value})],True)['status'])
    def test_cpp_or_spawn_override_not_overwritten(self):
        self.assertEqual('review',assess(Q,dict(N,ScriptName='custom'),[],True)['status'])
        self.assertEqual('review',assess(Q,N,[],True,True)['status'])
    def test_unknown_contract_not_repaired(self):
        self.assertEqual('review',assess(dict(Q,RequiredNpcOrGo1=47057),N,[],True)['status'])
        self.assertEqual('incomplete',assess(Q,N,[],False)['status'])


class DiscoveryTests(unittest.TestCase):
    def test_other_quest_found_without_a_registered_contract(self):
        from unittest.mock import MagicMock
        from training_audit import discover

        quest = {'ID': 123, 'LogTitle': 'Training', 'LogDescription': 'Cast on a Training Watcher.',
                 'RequiredSpell': 774}
        for slot in range(1, 5):
            quest[f'ObjectiveText{slot}'] = 'Practice Rejuvenation' if slot == 2 else ''
        quest.update(RequiredNpcOrGo2=456, RequiredNpcOrGoCount2=1)
        target = {'entry':789, 'name':'Training Watcher', 'AIName':'', 'ScriptName':''}
        connection = MagicMock()
        cursor = connection.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchall.side_effect = [[quest], [target], []]
        result, = discover(connection)
        self.assertEqual((123, 2, 789, 456, 'review'),
                         (result['quest'], result['objective_slot'], result['target']['entry'],
                          result['credit'], result['status']))
        self.assertFalse(result['automatic_apply'])
