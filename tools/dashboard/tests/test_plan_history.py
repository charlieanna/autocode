"""Saved-state regressions for Astra assignment and brief history."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent_console import astra_plan_state


class PlanHistoryTests(unittest.TestCase):
 def feedback_replacement_fixture(self):
   """Return the producer-shaped feedback replacement fixture owned by this test.

   This is a portable transcription of the fixture-only runner state formerly
   supplied as operator evidence.  Revision 2 deliberately remains ``draft``
   after feedback even though its recorded ``r2`` approval event survives.  The
   latter is required to exercise exact-event historical provenance; it does
   not authorize the revision-3 replacement draft.
   """
   body={
    'intended_outcome':'Provide a deterministic greeting CLI',
    'intended_user':'A local developer',
    'end_to_end_flow':['Run the CLI with a name','Read the greeting or an invalid-input error'],
    'technical_approach':['A standard-library Python CLI using sys.argv'],
    'deliverables':['greet.py','CLI regression tests'],
    'required_behaviors':['Print Hello, NAME for a nonempty name'],
    'important_failure_cases':['Reject an empty name with nonzero exit status'],
    'scope_exclusions':['Web service','Deployment'],
    'constraints':['Python standard library only'],
    'permission_boundaries':['Read and edit only this fixture Git workspace; no external writes'],
    'accepted_assumptions':[{'text':'CLI invocation is sufficient','basis':'agent_proposed','answer_id':''}],
    'delegated_decisions':[],
    'acceptance_criteria':[{'id':'C1','criterion':'Contract holds','verification_method':'Execute greeting and invalid-input regression checks','human_review':False}],
    'open_blocking_questions':[]}
   original=copy.deepcopy(body)
   original['milestones']=[{'id':'M1','objective':'Deliver and verify the greeting flow','acceptance_criteria':['C1']}]
   replacement=copy.deepcopy(body)
   replacement['milestones']=[{'id':'M1','objective':'Revised milestone after feedback','acceptance_criteria':['C1']}]
   first_token='r2:5f6af368be72d8be6664c9038c235b0812b9d5b000c128dcb40e825bfab63522'
   replacement_token='r3:2c3e14a5b24adcd7eef17c16af071beaf85150dc8c7683137803f42fc143ff9d'
   feedback={'kind':'brief_feedback','id':'intervention-fixture-feedback','actor':'user_intervention','at':'2026-09-19T23:00:00Z',
             'text':'Revise the plan','contract_token':first_token,'receipt_id':'fixture-feedback',
             'retained_work':{'current_task':None,'stages':0}}
   return {
    'version':3,'workspace':'/disposable/fixture','task':'Fixture only','status':'RUNNING','iteration':1,
    'user_events':[{'kind':'goal_approval','actor':'user_cli','at':'2026-09-19T23:02:33.090784+00:00','token':first_token},
                   feedback,
                   {'kind':'goal_approval','actor':'user_cli','at':'2026-09-19T23:02:33.091084+00:00','token':replacement_token}],
    'goal_contract':{'task_id':'de408e8b-5bbe-418b-bf3d-3f72d6467632','revision':3,'body':replacement,
                     'hash':replacement_token.split(':',1)[1],'approval_status':'approved',
                     'approval_event':{'kind':'goal_approval','actor':'user_cli','at':'2026-09-19T23:02:33.091084+00:00','token':replacement_token},
                     'origin':'fixture-replanning','created_at':'2026-09-19T23:02:33.090895+00:00'},
    'contract_history':[
     {'task_id':'de408e8b-5bbe-418b-bf3d-3f72d6467632','revision':1,'body':{'intended_outcome':'Fixture only'},
      'hash':'c17f389cbe792d7844a09259b5176841343b319ae7e53e78ed2b38779dec5d9b','approval_status':'draft','approval_event':None,
      'origin':'migration_draft; no inferred user approval','created_at':'2026-09-19T23:02:33.090288+00:00'},
     {'task_id':'de408e8b-5bbe-418b-bf3d-3f72d6467632','revision':2,'body':original,
      'hash':first_token.split(':',1)[1],'approval_status':'draft','approval_event':None,'origin':'fixture','created_at':'2026-09-19T23:02:33.090561+00:00'}],
    'brief_feedback':[copy.deepcopy(feedback)],'stop_reason':'Queued feedback was applied; explicitly continue to Astra discovery.',
    'displayed_goal':replacement_token,'displayed_review':None}
 def fixture(self):
  first={'id':'task-first','contract_revision':1,'assigned_at':'2026-09-19T10:00:00.000001+00:00',
         'objective':'Implement the first step','requirements':['Preserve existing behavior'],
         'validation_plan':['Run the relevant tests'],'decision':'CONTINUE'}
  rework={'id':'task-rework','contract_revision':1,'assigned_at':'2026-09-19T11:00:00.000001+00:00',
          'objective':'Correct the failed case','requirements':['Fix the observed failure'],
          'validation_plan':['Reproduce then verify the correction'],'decision':'REWORK'}
  original={'revision':1,'hash':'first','approval_status':'approved','approval_event':{'at':'2026-09-19T09:00:00+00:00'},
            'body':{'milestones':[{'id':'M1','objective':'Initial approved milestone'}],'intended_outcome':'Original outcome'}}
  current={'revision':2,'hash':'second','approval_status':'draft','approval_event':None,
           'body':{'milestones':[{'id':'M2','objective':'Proposed changed milestone'}],'intended_outcome':'Changed outcome'}}
  return {'task_archive':[first],'current_task':copy.deepcopy(rework),'plan':['Latest recorded plan'],
           'contract_history':[original],'goal_contract':current,
           'user_events':[{'kind':'goal_approval','token':'r1:first'}],
          'decisions':[{'at':'2026-09-19T10:00:00.000099+00:00','iteration':1,'current_task':copy.deepcopy(first),'next_stage':'terra',
                        'report':{'status':'CONTINUE','contract_revision':1,'plan':['First assigned plan'],'evidence':['Planning evidence']}},
                       {'at':'2026-09-19T11:00:00.000099+00:00','iteration':2,'current_task':copy.deepcopy(rework),'next_stage':'terra',
                        'report':{'status':'REWORK','contract_revision':1,'plan':['Rework plan'],'evidence':['The test exposed the missing case']}}]}

 def test_assignment_ids_deduplicate_different_assignment_and_review_timestamps(self):
  state=self.fixture();before=copy.deepcopy(state);view=astra_plan_state(state)
  self.assertEqual(['task-first','task-rework'],[row['id'] for row in view['history']])
  self.assertEqual(state['task_archive'][0]['assigned_at'],view['history'][0]['timestamp'])
  self.assertEqual('terra',view['current_assignment']['next_role'])
  self.assertEqual('The test exposed the missing case',view['current_assignment']['reason'])
  self.assertEqual(before,state)

 def test_approved_initial_brief_survives_proposed_revision_and_keeps_full_bodies(self):
  state=self.fixture();view=astra_plan_state(state)
  self.assertEqual(['Initial approved milestone'],view['initial_plan'])
  self.assertEqual('approved',view['initial_plan_approval'])
  self.assertEqual([(1,'approved',False),(2,'draft',True)],
                   [(b['revision'],b['approval_status'],b['current']) for b in view['briefs']])
  self.assertEqual('Original outcome',view['briefs'][0]['body']['intended_outcome'])
  self.assertEqual('Changed outcome',view['briefs'][1]['body']['intended_outcome'])

 def test_historical_approval_uses_matching_goal_event_after_feedback_invalidation(self):
  state=self.feedback_replacement_fixture();state['user_events'].append(copy.deepcopy(state['user_events'][0]));before=copy.deepcopy(state);view=astra_plan_state(state)
  self.assertEqual(['Deliver and verify the greeting flow'],view['initial_plan'])
  self.assertEqual('historically approved/inactive',view['initial_plan_approval'])
  self.assertEqual([(1,False),(2,True),(3,True)],[(brief['revision'],brief['historically_approved']) for brief in view['briefs']])
  self.assertEqual('draft',view['briefs'][1]['approval_status'])
  self.assertEqual('Revised milestone after feedback',view['briefs'][2]['body']['milestones'][0]['objective'])
  self.assertEqual('approved',view['current_plan_approval'])
  self.assertEqual(before,state)

 def test_replacement_stays_draft_until_its_own_exact_approval_event(self):
  state=self.feedback_replacement_fixture();state['user_events']=state['user_events'][:2];state['goal_contract']['approval_status']='draft'
  draft=astra_plan_state(state)
  self.assertEqual(['Deliver and verify the greeting flow'],draft['initial_plan'])
  self.assertEqual('draft',draft['current_plan_approval'])
  self.assertFalse(draft['briefs'][-1]['historically_approved'])
  state['user_events'].append({'kind':'goal_approval','token':'r3:2c3e14a5b24adcd7eef17c16af071beaf85150dc8c7683137803f42fc143ff9d'})
  state['goal_contract']['approval_status']='approved';approved=astra_plan_state(state)
  self.assertEqual(['Deliver and verify the greeting flow'],approved['initial_plan'])
  self.assertTrue(approved['briefs'][-1]['historically_approved'])
  self.assertEqual('approved',approved['current_plan_approval'])

 def test_only_exact_goal_approval_event_establishes_historical_approval(self):
  state=self.feedback_replacement_fixture();original=state['user_events'][0]
  for event in ({}, {'kind':'goal_approval','token':'r2:wrong'}, {'kind':'goal_approval','token':'r3:5f6af368be72d8be6664c9038c235b0812b9d5b000c128dcb40e825bfab63522'}, {'kind':'brief_feedback','token':original['token']}, {'kind':'goal_approval','token':'r2:5f6af368be72d8be6664c9038c235b0812b9d5b000c128dcb40e825bfab63522x'}, {'kind':'goal_approval','token':None}):
   state['user_events']=[event];view=astra_plan_state(state)
   self.assertNotEqual(['Deliver and verify the greeting flow'],view['initial_plan'])
   self.assertFalse(any(brief['historically_approved'] for brief in view['briefs']))

 def test_review_without_new_assignment_keeps_its_plan_and_evidence(self):
  state=self.fixture();state['decisions'].append({'at':'2026-09-19T12:00:00+00:00','iteration':2,'next_stage':'sol',
   'report':{'status':'VALIDATE','contract_revision':1,'plan':['Independently check the repair'],'evidence':['Ready for independent validation']}})
  view=astra_plan_state(state)
  self.assertEqual(2,len(view['history']))
  self.assertEqual(3,len(view['decisions']))
  self.assertEqual(['Independently check the repair'],view['decisions'][-1]['plan'])
  self.assertEqual('Ready for independent validation',view['decisions'][-1]['reason'])
  self.assertEqual('sol',view['decisions'][-1]['next_stage'])

 def test_legacy_missing_fields_do_not_invent_approval_owner_or_assignments(self):
  view=astra_plan_state({'plan':['Legacy current plan'],'decisions':[None,{'report':{'plan':['Earlier recorded plan']}}],
                         'task_archive':[None,{'id':[]}],'contract_history':['legacy'],'current_task':None})
  self.assertEqual([],view['history']);self.assertIsNone(view['current_assignment'])
  self.assertIsNone(view['initial_plan_approval']);self.assertEqual([],view['briefs'])
  self.assertEqual(['Earlier recorded plan'],view['initial_plan'])


if __name__=='__main__':unittest.main()
