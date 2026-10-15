import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest


FIXTURE = r'''
#include "pocket_reminders.h"
#include <assert.h>
#include <limits.h>
#include <math.h>
#include <stdio.h>
#include <string.h>
static const int64_t NOW=1800000000;
static pocket_reminders_state state;
static pocket_reminder_event events[POCKET_REMINDER_EVENT_LIMIT];
static pocket_usage_snapshot source(void){
 pocket_usage_snapshot s={0};
 for(unsigned p=0;p<3;p++){
  s.providers[p].availability=POCKET_USAGE_AVAILABLE;s.providers[p].metric_count=1;
  s.providers[p].metrics[0]=(pocket_usage_metric){.has_value=true,.value=79,.has_fetched_at=true,.fetched_at=NOW-10,
   .has_expires_at=true,.expires_at=NOW+30,.has_resets_at=true,.resets_at=NOW+60,.window_minutes=300,
   .unit=POCKET_USAGE_PERCENT_USED,.category=POCKET_USAGE_SUBSCRIPTION,.availability=POCKET_USAGE_AVAILABLE};
  snprintf(s.providers[p].metrics[0].window,49,"primary");
 }
 return s;
}
static size_t run(pocket_usage_snapshot *s,int64_t now,unsigned target,unsigned threshold){
 const pocket_reminder_config config={.percent_used_threshold=threshold};
 memset(events,0,sizeof events);return pocket_reminders_evaluate(&state,s,now,target,&config,events,POCKET_REMINDER_EVENT_LIMIT);
}
static void test_percent_and_dedupe(void){
 pocket_usage_snapshot s=source();pocket_reminders_init(&state);
 assert(run(&s,NOW,0,80)==0);s.providers[0].metrics[0].value=80;
 assert(run(&s,NOW,0,80)==1);assert(events[0].kind==POCKET_REMINDER_THRESHOLD);
 assert(events[0].target==0&&events[0].provider==0&&events[0].metric==0&&events[0].threshold==80);
 assert(events[0].has_value&&events[0].value==80&&events[0].unit==POCKET_USAGE_PERCENT_USED&&events[0].category==POCKET_USAGE_SUBSCRIPTION);
 assert(run(&s,NOW,0,80)==0);s.providers[0].metrics[0].value=99;assert(run(&s,NOW,0,90)==0);
 assert(run(&s,NOW,1,90)==1);assert(events[0].target==1);
 s.providers[1].metrics[0].unit=POCKET_USAGE_PERCENT_REMAINING;s.providers[1].metrics[0].value=10;
 assert(run(&s,NOW,0,90)==1);assert(events[0].provider==1&&events[0].value==10&&events[0].unit==POCKET_USAGE_PERCENT_REMAINING&&events[0].threshold==10);
 s.providers[2].metrics[0].unit=POCKET_USAGE_PERCENT_REMAINING;s.providers[2].metrics[0].value=0;
 assert(run(&s,NOW,0,100)==1&&events[0].value==0&&events[0].unit==POCKET_USAGE_PERCENT_REMAINING&&events[0].threshold==0);
 pocket_reminders_clear_target(&state,1);assert(run(&s,NOW,1,100)==1&&events[0].provider==2);
}
static void test_unknown_and_credit(void){
 pocket_usage_snapshot s=source();pocket_reminders_init(&state);
 for(unsigned p=0;p<3;p++)s.providers[p].metrics[0].value=99;
 s.providers[0].metrics[0].unit=POCKET_USAGE_USD_CREDIT;s.providers[0].metrics[0].category=POCKET_USAGE_INCLUDED_CREDIT;
 s.providers[1].metrics[0].unit=POCKET_USAGE_USD_CREDIT;s.providers[1].metrics[0].category=POCKET_USAGE_PURCHASED_CREDIT;
 s.providers[2].metrics[0].has_value=false;assert(run(&s,NOW,0,80)==0);
 const pocket_usage_availability bad[]={POCKET_USAGE_UNFETCHED,POCKET_USAGE_UNAVAILABLE,POCKET_USAGE_NOT_CONFIGURED,POCKET_USAGE_STALE,POCKET_USAGE_FAILED};
 for(unsigned i=0;i<sizeof bad/sizeof bad[0];i++){
  s=source();s.providers[0].metrics[0].value=99;s.providers[0].availability=bad[i];pocket_reminders_init(&state);assert(run(&s,NOW,0,80)==0);
  s.providers[0].availability=POCKET_USAGE_AVAILABLE;s.providers[0].metrics[0].availability=bad[i];assert(run(&s,NOW,0,80)==0);
 }
 s=source();s.providers[0].metrics[0].value=99;s.providers[0].availability=POCKET_USAGE_PARTIAL;
 pocket_reminders_init(&state);assert(run(&s,NOW,0,80)==1);
 s.providers[0].metrics[0].availability=POCKET_USAGE_PARTIAL;pocket_reminders_init(&state);assert(run(&s,NOW,0,80)==0);
 const double values[]={-1,101,NAN,INFINITY};
 for(unsigned i=0;i<sizeof values/sizeof values[0];i++){
  s=source();s.providers[0].metrics[0].value=values[i];pocket_reminders_init(&state);assert(run(&s,NOW,0,80)==0);
 }
}
static void test_timestamp_guards(void){
 for(unsigned i=0;i<10;i++){
  pocket_usage_snapshot s=source();s.providers[0].metrics[0].value=99;pocket_usage_metric *m=&s.providers[0].metrics[0];
  switch(i){
  case 0:m->has_fetched_at=false;break;case 1:m->has_expires_at=false;break;case 2:m->fetched_at=NOW+1;break;
  case 3:m->expires_at=NOW;break;case 4:m->fetched_at=-1;break;case 5:m->expires_at=-1;break;
  case 6:m->expires_at=INT64_MAX;break;case 7:m->fetched_at=0;break;case 8:m->resets_at=INT64_MAX;break;
  case 9:m->resets_at=-1;break;
  }
  pocket_reminders_init(&state);assert(run(&s,NOW,0,80)==0);assert(run(&s,NOW+120,0,80)==0);
 }
 pocket_usage_snapshot s=source();s.providers[0].metrics[0].value=99;
 pocket_reminders_init(&state);assert(run(&s,0,0,80)==0);assert(run(&s,INT64_MAX,0,80)==0);
 s.providers[0].metrics[0].has_resets_at=false;pocket_reminders_init(&state);assert(run(&s,NOW,0,80)==1&&!events[0].has_resets_at);
 s.providers[0].metrics[0].has_resets_at=true;s.providers[0].metrics[0].resets_at=NOW;
 pocket_reminders_init(&state);assert(run(&s,NOW,0,80)==1); /* Reset is not cache expiry. */
}
static void test_reset_not_quota(void){
 pocket_usage_snapshot s=source();pocket_reminders_init(&state);
 for(unsigned p=0;p<3;p++)s.providers[p].metrics[0].expires_at=NOW+120;
 assert(run(&s,NOW,0,80)==0);
 for(unsigned p=0;p<3;p++){s.providers[p].availability=POCKET_USAGE_FAILED;s.providers[p].metrics[0].availability=POCKET_USAGE_STALE;}
 assert(run(&s,NOW+60,0,80)==0);
 for(unsigned p=0;p<3;p++){s.providers[p].availability=POCKET_USAGE_AVAILABLE;s.providers[p].metrics[0].availability=POCKET_USAGE_AVAILABLE;}
 assert(run(&s,NOW+60,0,80)==3);
 for(unsigned i=0;i<3;i++){assert(events[i].kind==POCKET_REMINDER_RESET_REFRESH&&!events[i].has_value);assert(events[i].resets_at==NOW+60);}
 assert(strcmp(pocket_reminder_message(events[0].kind),"Reset time reached; refresh needed")==0);
 assert(run(&s,NOW+61,0,80)==0);
 pocket_reminders_init(&state);assert(run(&s,NOW+60,0,80)==0); /* No observed future reset. */
 for(unsigned p=0;p<3;p++)s.providers[p].availability=POCKET_USAGE_FAILED;
 pocket_reminders_init(&state);assert(run(&s,NOW,0,80)==0);assert(run(&s,NOW+60,0,80)==0); /* Failed cache cannot arm. */
 s=source();pocket_reminders_init(&state);assert(run(&s,NOW,0,80)==0);
 for(unsigned p=0;p<3;p++)s.providers[p].availability=POCKET_USAGE_NOT_CONFIGURED;
 assert(run(&s,NOW+60,0,80)==0);
}
static void test_reset_after_fresh_period_advance(void){
 pocket_usage_snapshot s=source();s.providers[1].metric_count=s.providers[2].metric_count=0;
 pocket_usage_metric *m=&s.providers[0].metrics[0];m->expires_at=m->resets_at;m->value=90;
 pocket_reminders_init(&state);assert(run(&s,NOW,0,80)==1&&events[0].kind==POCKET_REMINDER_THRESHOLD);
 s.providers[0].availability=POCKET_USAGE_STALE;m->availability=POCKET_USAGE_STALE;
 assert(run(&s,NOW+60,0,80)==0); /* Production min(TTL,reset) expiry: old period stays silent. */
 s.providers[0].availability=POCKET_USAGE_AVAILABLE;m->availability=POCKET_USAGE_AVAILABLE;
 m->fetched_at=NOW+60;m->expires_at=NOW+120;m->resets_at=NOW+120;m->value=79;
 assert(run(&s,NOW+60,0,80)==1); /* Fresh next period confirms the old known deadline. */
 assert(events[0].kind==POCKET_REMINDER_RESET_REFRESH&&events[0].resets_at==NOW+60&&!events[0].has_value);
 assert(strcmp(pocket_reminder_message(events[0].kind),"Reset time reached; refresh needed")==0);
 assert(run(&s,NOW+60,0,80)==0);m->value=90;
 assert(run(&s,NOW+61,0,80)==1&&events[0].kind==POCKET_REMINDER_THRESHOLD&&events[0].resets_at==NOW+120);
 assert(run(&s,NOW+61,0,80)==0);assert(run(&s,NOW+120,0,80)==0);
 m->fetched_at=NOW+120;m->expires_at=NOW+180;m->resets_at=NOW+180;m->value=79;
 assert(run(&s,NOW+120,0,80)==1&&events[0].kind==POCKET_REMINDER_RESET_REFRESH&&events[0].resets_at==NOW+120);
 assert(run(&s,NOW+120,0,80)==0); /* The next reset was armed, then deduplicated. */

 s=source();s.providers[1].metric_count=s.providers[2].metric_count=0;m=&s.providers[0].metrics[0];m->expires_at=m->resets_at;
 pocket_reminders_init(&state);assert(run(&s,NOW,0,80)==0);
 m->fetched_at=NOW+60;m->expires_at=NOW+120;m->resets_at=NOW+120;m->value=90;
 const pocket_reminder_config config={.percent_used_threshold=80};
 assert(pocket_reminders_evaluate(&state,&s,NOW+60,0,&config,NULL,0)==0);
 assert(pocket_reminders_evaluate(&state,&s,NOW+60,0,&config,events,1)==1&&events[0].kind==POCKET_REMINDER_RESET_REFRESH);
 assert(events[0].resets_at==NOW+60);
 assert(pocket_reminders_evaluate(&state,&s,NOW+60,0,&config,events,1)==1&&events[0].kind==POCKET_REMINDER_THRESHOLD);
 assert(events[0].resets_at==NOW+120);
 assert(pocket_reminders_evaluate(&state,&s,NOW+60,0,&config,events,1)==0);
}
static void test_new_cycle_reorder_replay(void){
 pocket_usage_snapshot s=source();pocket_reminders_init(&state);pocket_usage_provider *p=&s.providers[0];
 p->metric_count=2;p->metrics[1]=p->metrics[0];snprintf(p->metrics[1].window,49,"secondary");p->metrics[1].window_minutes=10080;
 p->metrics[0].value=p->metrics[1].value=99;assert(run(&s,NOW,0,80)==2);
 pocket_usage_metric swap=p->metrics[0];p->metrics[0]=p->metrics[1];p->metrics[1]=swap;
 assert(run(&s,NOW,0,80)==0);p->metric_count=1;assert(run(&s,NOW,0,80)==0);p->metric_count=2;assert(run(&s,NOW,0,80)==0);
 for(unsigned i=0;i<2;i++){p->metrics[i].resets_at=NOW+120;p->metrics[i].fetched_at=NOW;p->metrics[i].expires_at=NOW+90;}
 assert(run(&s,NOW+1,0,80)==2); /* Supersedes old schedule, no reset claim. */
 p->metrics[0].resets_at=NOW+60;assert(run(&s,NOW+2,0,80)==0); /* Old cycle cannot rearm. */
 s=source();pocket_reminders_init(&state);p=&s.providers[0];p->metrics[0].value=99;p->metrics[0].has_resets_at=false;
 assert(run(&s,NOW,0,80)==1&&!events[0].has_resets_at);
 p->metrics[0].has_resets_at=true;assert(run(&s,NOW,0,80)==0); /* Metadata enrichment is not a second alert. */
 p->metrics[0].has_resets_at=false;assert(run(&s,NOW,0,80)==0);
 p->metrics[0].has_resets_at=true;p->metrics[0].resets_at=NOW+120;p->metrics[0].fetched_at=NOW;
 assert(run(&s,NOW+1,0,80)==1);
 s=source();pocket_reminders_init(&state);s.providers[0].metrics[0].fetched_at=NOW;
 assert(run(&s,NOW,0,80)==0);s.providers[0].metrics[0].value=99;s.providers[0].metrics[0].fetched_at=NOW-5;
 assert(run(&s,NOW,0,80)==0); /* Replayed older sample cannot create a quota alert. */
}
static void test_disable_and_clocks(void){
 pocket_usage_snapshot s=source();pocket_reminders_init(&state);
 for(unsigned p=0;p<3;p++)s.providers[p].metrics[0].expires_at=NOW+120;
 assert(run(&s,NOW,0,0)==0);assert(run(&s,NOW+60,0,0)==0);assert(run(&s,NOW+61,0,80)==0);
 s=source();pocket_reminders_init(&state);assert(run(&s,NOW,0,80)==0);assert(run(&s,NOW+1,0,0)==0);
 for(unsigned p=0;p<3;p++)s.providers[p].metrics[0].expires_at=NOW+120;
 assert(run(&s,NOW+60,0,80)==0);
 s=source();pocket_reminders_init(&state);assert(run(&s,NOW,0,80)==0);
 for(unsigned p=0;p<3;p++)s.providers[p].metrics[0].expires_at=NOW+120;
 assert(run(&s,NOW-20,0,80)==0);assert(run(&s,NOW+60,0,80)==0); /* Rollback disarms. */
 s=source();pocket_reminders_init(&state);assert(run(&s,NOW,0,80)==0);assert(run(&s,0,0,80)==0);
 for(unsigned p=0;p<3;p++)s.providers[p].metrics[0].expires_at=NOW+120;
 assert(run(&s,NOW+60,0,80)==0);
 s=source();s.providers[0].metrics[0].value=99;pocket_reminders_init(&state);
 assert(run(&s,NOW,0,80)==1);assert(run(&s,NOW+1,0,0)==0);assert(run(&s,NOW+2,0,80)==0); /* Toggle never duplicates. */
}
static void test_capacity_duplicates_and_bounds(void){
 pocket_usage_snapshot s=source();pocket_reminders_init(&state);s.providers[0].metrics[0].value=99;
 const pocket_reminder_config config={.percent_used_threshold=80};
 assert(pocket_reminders_evaluate(&state,&s,NOW,0,&config,NULL,0)==0);assert(run(&s,NOW,0,80)==1);
 s=source();pocket_reminders_init(&state);assert(run(&s,NOW,0,80)==0);
 for(unsigned p=0;p<3;p++)s.providers[p].metrics[0].expires_at=NOW+120;
 assert(pocket_reminders_evaluate(&state,&s,NOW+60,0,&config,events,1)==1);assert(run(&s,NOW+60,0,80)==2);
 s=source();pocket_reminders_init(&state);s.providers[0].metric_count=2;
 s.providers[0].metrics[0].value=90;s.providers[0].metrics[1]=s.providers[0].metrics[0];
 s.providers[0].metrics[1].unit=POCKET_USAGE_PERCENT_REMAINING;s.providers[0].metrics[1].value=10;
 assert(run(&s,NOW,0,80)==1);
 pocket_reminders_init(&state);s.providers[0].metrics[1].value=90;assert(run(&s,NOW,0,80)==0); /* Conflicting observations. */
 s=source();pocket_reminders_init(&state);s.providers[0].metrics[0].value=99;
 for(unsigned i=0;i<5;i++){snprintf(s.providers[0].metrics[0].window,49,"window%u",i);assert(run(&s,NOW,0,80)==(i<4?1u:0u));}
 snprintf(s.providers[0].metrics[0].window,49,"window0");assert(run(&s,NOW,0,80)==0);
 s.providers[0].metric_count=UINT_MAX;assert(run(&s,NOW,0,80)==0);
 assert(run(NULL,NOW,0,80)==0);assert(run(&s,NOW,2,80)==0);assert(run(&s,NOW,0,101)==0);
 assert(pocket_reminders_evaluate(NULL,&s,NOW,0,&config,events,12)==0);
 pocket_reminders_clear_target(&state,UINT_MAX);pocket_reminders_init(NULL);
 assert(sizeof(pocket_reminders_state)<=1792);printf("state=%zu event=%zu\n",sizeof state,sizeof events[0]);
}
static void test_separate_usd_floor(void){
 pocket_usage_snapshot s=source();pocket_reminders_init(&state);
 s.providers[0].metrics[0].value=99;
 for(unsigned p=1;p<3;p++){
  s.providers[p].metrics[0].unit=POCKET_USAGE_USD_CREDIT;s.providers[p].metrics[0].value=p==1?5:0;
  s.providers[p].metrics[0].category=p==1?POCKET_USAGE_INCLUDED_CREDIT:POCKET_USAGE_PURCHASED_CREDIT;
 }
 pocket_reminder_config config={.usd_floor_enabled=true,.usd_floor=5};
 size_t count=pocket_reminders_evaluate(&state,&s,NOW,0,&config,events,12);assert(count==2);
 assert(events[0].unit==POCKET_USAGE_USD_CREDIT&&events[0].threshold==5&&events[0].value==5&&events[0].has_value);
 assert(events[1].unit==POCKET_USAGE_USD_CREDIT&&events[1].value==0&&events[1].category==POCKET_USAGE_PURCHASED_CREDIT);
 assert(pocket_reminders_evaluate(&state,&s,NOW,0,&config,events,12)==0);
 pocket_reminders_init(&state);config.usd_floor=0;assert(pocket_reminders_evaluate(&state,&s,NOW,0,&config,events,12)==1);
 assert(events[0].value==0&&events[0].threshold==0);
 pocket_reminders_init(&state);config.usd_floor=NAN;assert(pocket_reminders_evaluate(&state,&s,NOW,0,&config,events,12)==0);
 config.usd_floor=-1;assert(pocket_reminders_evaluate(&state,&s,NOW,0,&config,events,12)==0);
 config.usd_floor=INFINITY;assert(pocket_reminders_evaluate(&state,&s,NOW,0,&config,events,12)==0);
 pocket_reminders_init(&state);config.usd_floor=5;s.providers[1].metric_count=2;
 s.providers[1].metrics[1]=s.providers[2].metrics[0];s.providers[2].metric_count=0;
 assert(pocket_reminders_evaluate(&state,&s,NOW,0,&config,events,12)==2);
 assert(events[0].provider==1&&events[1].provider==1&&events[0].category!=events[1].category);
}
int main(void){
 test_percent_and_dedupe();test_unknown_and_credit();test_timestamp_guards();test_reset_not_quota();
 test_reset_after_fresh_period_advance();test_new_cycle_reorder_replay();test_disable_and_clocks();test_capacity_duplicates_and_bounds();test_separate_usd_floor();
 puts("pocket reminders: PASS");return 0;
}
'''


class FirmwareReminderTests(unittest.TestCase):
    def test_bounded_reminder_evaluator(self):
        compiler = os.environ.get('POCKET_HOST_CC') or shutil.which('gcc') or shutil.which('clang')
        cached = pathlib.Path(r'C:\Program Files\LLVM\bin\clang.exe')
        if not compiler and cached.is_file():
            compiler = str(cached)
        if not compiler:
            self.skipTest('host C compiler unavailable')
        root = pathlib.Path(__file__).resolve().parents[1] / 'firmware/core'
        with tempfile.TemporaryDirectory() as directory:
            fixture = pathlib.Path(directory) / 'test_reminders.c'
            fixture.write_text(FIXTURE, encoding='utf-8')
            binary = pathlib.Path(directory) / ('test-reminders.exe' if os.name == 'nt' else 'test-reminders')
            built = subprocess.run([
                compiler, '-std=c11', '-Wall', '-Wextra', '-Werror', '-pedantic',
                '-I' + str(root), str(root / 'pocket_reminders.c'), str(fixture), '-o', str(binary),
            ], capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('pocket reminders: PASS', result.stdout)
            print(result.stdout.strip())


if __name__ == '__main__':
    unittest.main()
