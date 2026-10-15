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
int main(void) {
 test_states();test_units_and_windows();test_freshness_and_errors();test_bounded_invalid_data();
 puts("pocket usage formatter: PASS");return 0;
}
