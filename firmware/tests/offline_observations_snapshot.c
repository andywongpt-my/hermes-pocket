/* Actual UI/LVGL; generated numeric observations only. */
#define main legacy_snapshot_main
#include "ui_snapshot.c"
#undef main
static void fits(void){
 lv_point_t size;
 lv_text_get_size(&size,lv_label_get_text(body),lv_obj_get_style_text_font(body,0),0,0,200,LV_TEXT_FLAG_NONE);
 if(size.y>166)fprintf(stderr,"body height %ld: %s\n",(long)size.y,lv_label_get_text(body));
 assert(size.y<=166);
 lv_text_get_size(&size,lv_label_get_text(badge),&lv_font_montserrat_14,lv_obj_get_style_text_letter_space(badge,0),0,LV_COORD_MAX,LV_TEXT_FLAG_NONE);
 if(size.x>180)fprintf(stderr,"badge width %ld: %s\n",(long)size.x,lv_label_get_text(badge));
 assert(size.x<=180);
 lv_text_get_size(&size,lv_label_get_text(footer),lv_obj_get_style_text_font(footer,0),0,0,204,LV_TEXT_FLAG_NONE);assert(size.y<=40);
 const char *line=lv_label_get_text(footer);
 while(*line){const char *end=strchr(line,'\n');size_t n=end?(size_t)(end-line):strlen(line);char part[128];assert(n<sizeof part);memcpy(part,line,n);part[n]=0;
  lv_text_get_size(&size,part,lv_obj_get_style_text_font(footer,0),0,0,LV_COORD_MAX,LV_TEXT_FLAG_NONE);assert(size.x<=204);if(!end)break;line=end+1;}
 assert(!strstr(lv_label_get_text(body),"SECRET"));
}
int main(int argc,char **argv){
 int failed=legacy_snapshot_main(argc,argv);const char *out=argc>1?argv[1]:".";
 pocket_offline_diagnostic_snapshot s={.frames=1000,.samples=240000,.rms={32768,32768},.peak={32768,32768},.clips={240000,240000},.mean={-32768,-32768},.positive_clips={120000,120000},.negative_clips={120000,120000},.first_clip_sample={239998,239998},.last_clip_sample={239999,239999},.longest_clip_run={240000,240000}};
 s.early=(pocket_offline_diagnostic_window){.samples=2400,.rms={32768,32768},.peak={32768,32768},.clips={2400,2400},.mean={-32768,-32768}};
 s.late=s.early;s.late.samples=237600;s.late.clips[0]=s.late.clips[1]=237600;
 /* Deliberately maximal independent display values, not a physical waveform. */
 for(unsigned phase=0;phase<=PDIAG_CLEANUP_FAILED;phase++)for(unsigned page=0;page<POCKET_DIAGNOSTIC_PAGES;page++)for(unsigned cleanup=0;cleanup<3;cleanup++){
  s.phase=(pocket_offline_diagnostic_phase)phase;s.code=PDIAG_ADMISSION_FAILED;s.cleanup_attempted=cleanup!=0;s.restore_ok=cleanup==2;
  pocket_ui_view v={.page=POCKET_DIAGNOSTICS,.offline_diagnostic=&s,.uptime_ms=page*POCKET_DIAGNOSTIC_PAGE_MS,.battery_percent=76,.text="SECRET TRANSCRIPT",.reply="SECRET REPLY",.message="SECRET TOKEN"};
  pocket_ui_render(&v);advance(20);fits();
  if(phase==PDIAG_ERROR&&cleanup==2){char name[64];snprintf(name,sizeof name,"numeric-max-%u",page);failed|=save(out,name);}
 }
 s=(pocket_offline_diagnostic_snapshot){.phase=PDIAG_ERROR,.code=PDIAG_STOPPED,.frames=882,.samples=211680,.mean={-159,0},.rms={2683,0},.peak={32768,0},.clips={1030,0},.negative_clips={1030,0},.first_clip_sample={0,UINT32_MAX},.last_clip_sample={1029,UINT32_MAX},.longest_clip_run={1030,0},.cleanup_attempted=true,.restore_ok=true};
 s.early.samples=2400;s.early.clips[0]=1030;s.early.rms[0]=21493;s.early.peak[0]=32768;s.early.mean[0]=-14062;s.late.samples=209280;s.late.rms[0]=s.late.peak[0]=1409;
 for(unsigned page=0;page<POCKET_DIAGNOSTIC_PAGES;page++){
  pocket_ui_view v={.page=POCKET_DIAGNOSTICS,.offline_diagnostic=&s,.uptime_ms=page*POCKET_DIAGNOSTIC_PAGE_MS,.battery_percent=76};
  pocket_ui_render(&v);advance(20);fits();char name[64];snprintf(name,sizeof name,"numeric-example-%u",page);failed|=save(out,name);
 }
 return failed;
}
