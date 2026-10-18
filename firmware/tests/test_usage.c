#include "pocket_usage.h"
#include <assert.h>
#include <limits.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

static const int64_t NOW=1800000000;
static pocket_usage_provider sample(void) {
 pocket_usage_provider p={.availability=POCKET_USAGE_AVAILABLE,.metric_count=1};
 p.metrics[0]=(pocket_usage_metric){.has_value=true,.value=26,.has_fetched_at=true,.fetched_at=NOW-120,
  .has_resets_at=true,.resets_at=NOW+7200,.has_expires_at=true,.expires_at=NOW+60,
  .window_minutes=300,.unit=POCKET_USAGE_PERCENT_USED,.category=POCKET_USAGE_SUBSCRIPTION,.availability=POCKET_USAGE_AVAILABLE};
 snprintf(p.metrics[0].window,sizeof p.metrics[0].window,"primary");return p;
}
static void contains(const char *s,const char *part){assert(strstr(s,part)!=NULL);}
static void test_states(void) {
 pocket_usage_display d;pocket_usage_provider p={0};
 pocket_usage_format(NULL,0,NOW,&d);assert(strcmp(d.value,"Not fetched")==0);
 pocket_usage_format(&p,0,NOW,&d);assert(d.availability==POCKET_USAGE_UNFETCHED);contains(d.freshness,"No sample");
 p.availability=POCKET_USAGE_NOT_CONFIGURED;pocket_usage_format(&p,0,NOW,&d);assert(strcmp(d.value,"Not set up")==0);
 p.availability=POCKET_USAGE_UNAVAILABLE;pocket_usage_format(&p,0,NOW,&d);assert(strcmp(d.value,"Unavailable")==0);
 p.availability=POCKET_USAGE_FAILED;pocket_usage_format(&p,0,NOW,&d);assert(strcmp(d.value,"Unknown")==0);contains(d.detail,"failed");
}
static void test_units_and_windows(void) {
 pocket_usage_provider p=sample();pocket_usage_display d;
 pocket_usage_format(&p,0,NOW,&d);assert(strcmp(d.value,"26% used")==0);contains(d.detail,"5h");contains(d.detail,"2h");contains(d.freshness,"2m ago");
 p.metric_count=2;p.metrics[1]=p.metrics[0];p.metrics[1].value=0;p.metrics[1].unit=POCKET_USAGE_PERCENT_REMAINING;p.metrics[1].window_minutes=10080;
 pocket_usage_format(&p,1,NOW,&d);assert(strcmp(d.value,"0% left")==0);contains(d.detail,"7d");
 pocket_usage_format(&p,2,NOW,&d);assert(strcmp(d.value,"26% used")==0);
 p.metrics[1].has_value=false;pocket_usage_format(&p,1,NOW,&d);assert(strcmp(d.value,"Unknown")==0);assert(d.availability==POCKET_USAGE_PARTIAL);
 p.metrics[0].unit=POCKET_USAGE_USD_CREDIT;p.metrics[0].category=POCKET_USAGE_INCLUDED_CREDIT;p.metrics[0].value=12.34;
 pocket_usage_format(&p,0,NOW,&d);assert(strcmp(d.value,"$12.34 USD")==0);contains(d.detail,"Included");
 p.metrics[0].category=POCKET_USAGE_PURCHASED_CREDIT;p.metrics[0].value=0;
 pocket_usage_format(&p,0,NOW,&d);assert(strcmp(d.value,"$0.00 USD")==0);contains(d.detail,"Purchased");
 p.metrics[0].value=0.004;pocket_usage_format(&p,0,NOW,&d);assert(strcmp(d.value,"<$0.01 USD")==0);
 p=sample();p.metrics[0].value=12.25;pocket_usage_format(&p,0,NOW,&d);assert(strcmp(d.value,"12.25% used")==0);
 p.metrics[0].value=0.001;pocket_usage_format(&p,0,NOW,&d);assert(strcmp(d.value,"<0.01% used")==0);
 p=sample();p.metrics[0].window_minutes=90;pocket_usage_format(&p,0,NOW,&d);contains(d.detail,"90m");
 p=sample();p.metrics[0].value=-0.0;pocket_usage_format(&p,0,NOW,&d);assert(strcmp(d.value,"0% used")==0);
}
static void test_freshness_and_errors(void) {
 pocket_usage_provider p=sample();pocket_usage_display d;
 p.metrics[0].expires_at=NOW;pocket_usage_format(&p,0,NOW,&d);assert(d.availability==POCKET_USAGE_STALE);contains(d.value,"26%");contains(d.freshness,"Stale");
 p=sample();p.availability=POCKET_USAGE_FAILED;pocket_usage_format(&p,0,NOW,&d);assert(d.availability==POCKET_USAGE_FAILED);contains(d.value,"26%");contains(d.detail,"failed");contains(d.freshness,"cached");
 p=sample();p.availability=POCKET_USAGE_PARTIAL;pocket_usage_format(&p,0,NOW,&d);assert(d.availability==POCKET_USAGE_PARTIAL);contains(d.freshness,"Partial");
 p=sample();p.metrics[0].availability=POCKET_USAGE_STALE;pocket_usage_format(&p,0,NOW,&d);assert(d.availability==POCKET_USAGE_STALE);
 p=sample();p.metrics[0].availability=POCKET_USAGE_FAILED;pocket_usage_format(&p,0,NOW,&d);assert(d.availability==POCKET_USAGE_FAILED);contains(d.freshness,"cached");
 p=sample();p.metrics[0].fetched_at=NOW+1;pocket_usage_format(&p,0,NOW,&d);assert(d.availability==POCKET_USAGE_STALE);contains(d.freshness,"Clock mismatch");assert(strstr(d.freshness,"0s")==NULL);
 p=sample();p.metrics[0].has_fetched_at=false;pocket_usage_format(&p,0,NOW,&d);assert(d.availability==POCKET_USAGE_PARTIAL);contains(d.freshness,"time unknown");
 p=sample();pocket_usage_format(&p,0,0,&d);contains(d.freshness,"Clock unknown");
 p=sample();p.availability=POCKET_USAGE_FAILED;pocket_usage_format(&p,0,0,&d);contains(d.freshness,"Failed");contains(d.freshness,"Clock unknown");
 p=sample();p.availability=POCKET_USAGE_FAILED;p.metrics[0].availability=POCKET_USAGE_UNAVAILABLE;p.metrics[0].has_value=false;
 pocket_usage_format(&p,0,NOW,&d);assert(d.availability==POCKET_USAGE_FAILED);contains(d.detail,"failed");
 p=sample();p.metrics[0].has_resets_at=false;pocket_usage_format(&p,0,NOW,&d);contains(d.detail,"reset unknown");
 p=sample();p.metrics[0].resets_at=NOW;pocket_usage_format(&p,0,NOW,&d);contains(d.detail,"reset due");
}
static void test_bounded_invalid_data(void) {
 pocket_usage_provider p=sample();pocket_usage_display d;
 const double invalid[]={-1,101,NAN,INFINITY};
 for(unsigned i=0;i<sizeof invalid/sizeof invalid[0];i++) {
  p.metrics[0].value=invalid[i];pocket_usage_format(&p,0,NOW,&d);assert(strcmp(d.value,"Unknown")==0);assert(d.availability==POCKET_USAGE_PARTIAL);
 }
 p=sample();p.metric_count=UINT_MAX;pocket_usage_format(&p,UINT_MAX,NOW,&d);assert(strcmp(d.value,"Unknown")==0);
 p=sample();p.metrics[0].unit=(pocket_usage_unit)99;pocket_usage_format(&p,0,NOW,&d);assert(strcmp(d.value,"Unknown")==0);
 p=sample();p.metrics[0].category=(pocket_usage_category)99;pocket_usage_format(&p,0,NOW,&d);assert(strcmp(d.value,"Unknown")==0);
 p=sample();p.metrics[0].availability=(pocket_usage_availability)99;pocket_usage_format(&p,0,NOW,&d);assert(strcmp(d.value,"Unknown")==0);
 p=sample();p.metrics[0].unit=POCKET_USAGE_USD_CREDIT;p.metrics[0].category=POCKET_USAGE_PURCHASED_CREDIT;p.metrics[0].value=1e100;
 pocket_usage_format(&p,0,NOW,&d);assert(strcmp(d.value,"Unknown")==0);
 p=sample();p.metrics[0].window_minutes=0;memset(p.metrics[0].window,'x',sizeof p.metrics[0].window);
 pocket_usage_format(&p,0,NOW,&d);assert(d.detail[sizeof d.detail-1]=='\0');
 p=sample();p.metrics[0].window_minutes=0;snprintf(p.metrics[0].window,sizeof p.metrics[0].window,"https://private.example/key");
 pocket_usage_format(&p,0,NOW,&d);assert(strstr(d.detail,"https")==NULL);
 p=sample();p.metrics[0].fetched_at=INT64_MIN;p.metrics[0].resets_at=INT64_MAX;pocket_usage_format(&p,0,INT64_MAX,&d);
 assert(d.freshness[sizeof d.freshness-1]=='\0');
}
static pocket_usage_local local_sample(void) {
 pocket_usage_local local={.present=true,.availability=POCKET_USAGE_AVAILABLE,
  .main_known=(1u<<POCKET_USAGE_COUNTER_COUNT)-1,.auxiliary_known=(1u<<POCKET_USAGE_COUNTER_COUNT)-1,
  .coverage_known=(1u<<POCKET_USAGE_COVERAGE_COUNT)-1,.has_fetched_at=true,.fetched_at=NOW-120,
  .has_last_seen=true,.last_seen=NOW-86400*365};
 for(unsigned i=0;i<POCKET_USAGE_COUNTER_COUNT;i++){local.main[i]=100+i;local.auxiliary[i]=200+i;}
 for(unsigned i=0;i<POCKET_USAGE_COVERAGE_COUNT;i++)local.coverage[i]=300+i;
 return local;
}
static void test_local_exact_counters_and_groups(void) {
 const pocket_usage_counter order[]={POCKET_USAGE_API_CALLS,POCKET_USAGE_INPUT_TOKENS,POCKET_USAGE_OUTPUT_TOKENS,
  POCKET_USAGE_CACHE_READ_TOKENS,POCKET_USAGE_CACHE_WRITE_TOKENS,POCKET_USAGE_REASONING_TOKENS,POCKET_USAGE_RUNS};
 pocket_usage_local local=local_sample();pocket_usage_display d;
 for(unsigned page=0;page<7;page++){
  char main[32],aux[32];snprintf(main,sizeof main,"Main %u",100+(unsigned)order[page]);snprintf(aux,sizeof aux,"Aux %u",200+(unsigned)order[page]);
  pocket_usage_local_format(&local,page,NOW,&d);assert(!strcmp(d.value,main));assert(!strcmp(d.detail,aux));assert(d.availability==POCKET_USAGE_AVAILABLE);
 }
 local.main[POCKET_USAGE_API_CALLS]=UINT64_C(4294967297);local.auxiliary[POCKET_USAGE_API_CALLS]=POCKET_USAGE_COUNTER_MAX;
 pocket_usage_local_format(&local,0,NOW,&d);assert(!strcmp(d.value,"Main 4294967297"));assert(!strcmp(d.detail,"Aux 9007199254740991"));
 local.main[POCKET_USAGE_INPUT_TOKENS]=23;local.main[POCKET_USAGE_CACHE_READ_TOKENS]=17;local.main[POCKET_USAGE_CACHE_WRITE_TOKENS]=13;
 pocket_usage_local_format(&local,1,NOW,&d);assert(!strcmp(d.value,"Main 23"));
 pocket_usage_local_format(&local,3,NOW,&d);assert(!strcmp(d.value,"Main 17"));
 pocket_usage_local_format(&local,4,NOW,&d);assert(!strcmp(d.value,"Main 13"));
 local.main[POCKET_USAGE_API_CALLS]=POCKET_USAGE_COUNTER_MAX+1;
 pocket_usage_local_format(&local,0,NOW,&d);assert(!strcmp(d.value,"Main Unknown"));assert(d.availability==POCKET_USAGE_PARTIAL);
}
static void test_local_null_and_zero(void) {
 pocket_usage_local local=local_sample();pocket_usage_display d;
 pocket_usage_local_format(NULL,0,NOW,&d);assert(!strcmp(d.value,"Main Unknown"));assert(!strcmp(d.detail,"Aux Unknown"));assert(d.availability==POCKET_USAGE_UNFETCHED);
 local.present=false;pocket_usage_local_format(&local,0,NOW,&d);assert(!strcmp(d.value,"Main Unknown"));assert(!strcmp(d.detail,"Aux Unknown"));
 local.present=true;local.main_known=local.auxiliary_known=0;local.main[POCKET_USAGE_API_CALLS]=local.auxiliary[POCKET_USAGE_API_CALLS]=0;
 pocket_usage_local_format(&local,0,NOW,&d);assert(!strcmp(d.value,"Main Unknown"));assert(!strcmp(d.detail,"Aux Unknown"));assert(d.availability==POCKET_USAGE_PARTIAL);
 local.main_known=1u<<POCKET_USAGE_API_CALLS;pocket_usage_local_format(&local,0,NOW,&d);assert(!strcmp(d.value,"Main 0"));assert(!strcmp(d.detail,"Aux Unknown"));
 local.main_known=0;local.auxiliary_known=1u<<POCKET_USAGE_API_CALLS;pocket_usage_local_format(&local,0,NOW,&d);assert(!strcmp(d.value,"Main Unknown"));assert(!strcmp(d.detail,"Aux 0"));
 local.availability=POCKET_USAGE_UNAVAILABLE;pocket_usage_local_format(&local,0,NOW,&d);assert(d.availability==POCKET_USAGE_UNAVAILABLE);contains(d.freshness,"Unavailable");
}
static void test_local_available_with_one_observed_group(void) {
 const pocket_usage_counter order[]={POCKET_USAGE_API_CALLS,POCKET_USAGE_INPUT_TOKENS,POCKET_USAGE_OUTPUT_TOKENS,
  POCKET_USAGE_CACHE_READ_TOKENS,POCKET_USAGE_CACHE_WRITE_TOKENS,POCKET_USAGE_REASONING_TOKENS,POCKET_USAGE_RUNS};
 pocket_usage_display d;
 for(unsigned auxiliary_only=0;auxiliary_only<2;auxiliary_only++){
  pocket_usage_local local=local_sample();
  if(auxiliary_only)local.main_known=0;else local.auxiliary_known=0;
  for(unsigned page=0;page<7;page++){
   char observed[32];snprintf(observed,sizeof observed,"%s %u",auxiliary_only?"Aux":"Main",(auxiliary_only?200:100)+(unsigned)order[page]);
   pocket_usage_local_format(&local,page,NOW,&d);
   assert(!strcmp(d.value,auxiliary_only?"Main Unknown":observed));
   assert(!strcmp(d.detail,auxiliary_only?observed:"Aux Unknown"));
   assert(d.availability==POCKET_USAGE_AVAILABLE);contains(d.freshness,"Fetched");assert(!strstr(d.freshness,"Partial"));
  }
  if(auxiliary_only)local.auxiliary[POCKET_USAGE_API_CALLS]=0;else local.main[POCKET_USAGE_API_CALLS]=0;
  pocket_usage_local_format(&local,0,NOW,&d);assert(d.availability==POCKET_USAGE_AVAILABLE);
  assert(!strcmp(d.value,auxiliary_only?"Main Unknown":"Main 0"));assert(!strcmp(d.detail,auxiliary_only?"Aux 0":"Aux Unknown"));
  pocket_usage_local_format(&local,0,0,&d);assert(d.availability==POCKET_USAGE_PARTIAL);contains(d.freshness,"Clock unknown");
  local.has_fetched_at=false;pocket_usage_local_format(&local,0,NOW,&d);assert(d.availability==POCKET_USAGE_PARTIAL);contains(d.freshness,"Time unknown");
  local.has_fetched_at=true;local.fetched_at=NOW+1;pocket_usage_local_format(&local,0,NOW,&d);assert(d.availability==POCKET_USAGE_STALE);contains(d.freshness,"Clock mismatch");
  local.fetched_at=NOW-120;local.availability=POCKET_USAGE_PARTIAL;pocket_usage_local_format(&local,0,NOW,&d);assert(d.availability==POCKET_USAGE_PARTIAL);
  local.availability=POCKET_USAGE_FAILED;pocket_usage_local_format(&local,0,NOW,&d);assert(d.availability==POCKET_USAGE_FAILED);contains(d.freshness,"cached");
 }
}
static void test_local_coverage_and_titles(void) {
 static const char *titles[]={"CALLS / MAIN + AUX","INPUT / MAIN + AUX","OUTPUT / MAIN + AUX","CACHE READ / M + A","CACHE WRITE / M + A","REASONING / M + A","RUNS / MAIN + AUX",
  "COVERAGE / OWNED","COVERAGE / OBSERVED","COVERAGE / MISSING","COVERAGE / UNATTR.","COVERAGE / UNMAPPED","COVERAGE / PROVIDER","FETCHED / UTC","LAST SEEN / UTC"};
 assert(POCKET_USAGE_LOCAL_PAGES==15);assert(POCKET_USAGE_DASHBOARD_PAGES==19);
 pocket_usage_local local=local_sample();pocket_usage_display d;
 for(unsigned page=0;page<POCKET_USAGE_LOCAL_PAGES;page++)assert(!strcmp(pocket_usage_local_title(page),titles[page]));
 for(unsigned i=0;i<POCKET_USAGE_COVERAGE_COUNT;i++){
  char count[32];snprintf(count,sizeof count,"%u",300+i);pocket_usage_local_format(&local,7+i,NOW,&d);
  assert(!strcmp(d.value,count));assert(!strcmp(d.detail,i==POCKET_USAGE_PROVIDER_OBSERVED_RUNS?"This provider":"Device/target"));
  local.coverage_known&=(uint8_t)~(1u<<i);pocket_usage_local_format(&local,7+i,NOW,&d);assert(!strcmp(d.value,"Unknown"));
 }
 local.coverage_known=1u<<POCKET_USAGE_OWNED_RUNS;local.coverage[POCKET_USAGE_OWNED_RUNS]=POCKET_USAGE_COUNTER_MAX;
 pocket_usage_local_format(&local,7,NOW,&d);assert(!strcmp(d.value,"9007199254740991"));
 local.coverage[POCKET_USAGE_OWNED_RUNS]++;pocket_usage_local_format(&local,7,NOW,&d);assert(!strcmp(d.value,"Unknown"));
 pocket_usage_local_format(&local,UINT_MAX,NOW,&d);assert(!strcmp(d.value,"Unknown"));assert(d.availability==POCKET_USAGE_PARTIAL);
 assert(!strcmp(pocket_usage_local_title(UINT_MAX),"POCKET HISTORY"));
}
static void test_local_cached_and_clock_states(void) {
 pocket_usage_local local=local_sample();pocket_usage_display d;
 local.availability=POCKET_USAGE_STALE;pocket_usage_local_format(&local,0,NOW,&d);assert(!strcmp(d.value,"Main 101"));assert(!strcmp(d.detail,"Aux 201"));assert(d.availability==POCKET_USAGE_STALE);contains(d.freshness,"Stale");contains(d.freshness,"cached");
 local.availability=POCKET_USAGE_FAILED;pocket_usage_local_format(&local,0,NOW,&d);assert(!strcmp(d.value,"Main 101"));assert(d.availability==POCKET_USAGE_FAILED);contains(d.freshness,"Failed");contains(d.freshness,"cached");
 local=local_sample();pocket_usage_local_format(&local,0,NOW+86400*365,&d);assert(d.availability==POCKET_USAGE_AVAILABLE);
 pocket_usage_local_format(&local,14,NOW,&d);assert(d.availability==POCKET_USAGE_AVAILABLE);assert(strstr(d.freshness,"Stale")==NULL);
 local.fetched_at=NOW+1;pocket_usage_local_format(&local,0,NOW,&d);assert(d.availability==POCKET_USAGE_STALE);contains(d.freshness,"Clock mismatch");
 local.availability=POCKET_USAGE_FAILED;pocket_usage_local_format(&local,0,NOW,&d);assert(d.availability==POCKET_USAGE_FAILED);contains(d.freshness,"Failed");contains(d.freshness,"Clock mismatch");
 local=local_sample();pocket_usage_local_format(&local,0,0,&d);assert(d.availability==POCKET_USAGE_PARTIAL);contains(d.freshness,"Clock unknown");
 local.availability=POCKET_USAGE_FAILED;pocket_usage_local_format(&local,0,0,&d);assert(d.availability==POCKET_USAGE_FAILED);contains(d.freshness,"Failed");contains(d.freshness,"Clock unknown");
 local=local_sample();local.has_fetched_at=false;pocket_usage_local_format(&local,0,NOW,&d);assert(d.availability==POCKET_USAGE_PARTIAL);contains(d.freshness,"Time unknown");
 local.availability=(pocket_usage_availability)99;pocket_usage_local_format(&local,0,NOW,&d);assert(d.availability==POCKET_USAGE_PARTIAL);
}
static void test_local_utc_calendar_and_bounds(void) {
 const struct { int64_t seconds;const char *text; } dates[]={
  {0,"1970-01-01 00:00:00 UTC"},{951782400,"2000-02-29 00:00:00 UTC"},
  {4107542400,"2100-03-01 00:00:00 UTC"},{253402300799,"9999-12-31 23:59:59 UTC"}};
 pocket_usage_local local=local_sample();pocket_usage_display d;
 for(unsigned i=0;i<sizeof dates/sizeof dates[0];i++){
  local.fetched_at=local.last_seen=dates[i].seconds;
  pocket_usage_local_format(&local,13,INT64_MAX,&d);assert(!strcmp(d.value,dates[i].text));assert(!strcmp(d.detail,"Hermes runtime"));
  pocket_usage_local_format(&local,14,INT64_MAX,&d);assert(!strcmp(d.value,dates[i].text));assert(!strcmp(d.detail,"Hermes runtime"));
 }
 const int64_t invalid[]={INT64_MIN,-1,253402300800,INT64_MAX};
 for(unsigned i=0;i<sizeof invalid/sizeof invalid[0];i++){
  local.fetched_at=local.last_seen=invalid[i];pocket_usage_local_format(&local,13,NOW,&d);assert(!strcmp(d.value,"Time unknown"));assert(d.availability==POCKET_USAGE_PARTIAL);
  pocket_usage_local_format(&local,14,NOW,&d);assert(!strcmp(d.value,"Time unknown"));
 }
 local=local_sample();local.has_fetched_at=local.has_last_seen=false;
 pocket_usage_local_format(&local,13,NOW,&d);assert(!strcmp(d.value,"Time unknown"));
 pocket_usage_local_format(&local,14,NOW,&d);assert(!strcmp(d.value,"Time unknown"));
 local=local_sample();local.fetched_at=0;pocket_usage_local_format(&local,0,1,&d);assert(d.availability==POCKET_USAGE_AVAILABLE);contains(d.freshness,"1s ago");
 pocket_usage_local_format(&local,0,INT64_MIN,&d);contains(d.freshness,"Clock unknown");
 pocket_usage_local_format(&local,0,INT64_MAX,&d);assert(d.freshness[sizeof d.freshness-1]=='\0');
 pocket_usage_local_format(&local,0,NOW,NULL);
}
int main(int argc,char **argv) {
 if(argc==1||!strcmp(argv[1],"quota")){
  test_states();test_units_and_windows();test_freshness_and_errors();test_bounded_invalid_data();puts("pocket usage formatter: PASS");
 }
 if(argc==1||!strcmp(argv[1],"local")){
  test_local_exact_counters_and_groups();test_local_null_and_zero();test_local_available_with_one_observed_group();test_local_coverage_and_titles();test_local_cached_and_clock_states();test_local_utc_calendar_and_bounds();
  puts("pocket local usage formatter: PASS");
 }
 return 0;
}
