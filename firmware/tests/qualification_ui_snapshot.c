/* Fixed fake controller views only: no capture, USB, playback or network. */
#define main legacy_snapshot_main
#include "ui_snapshot.c"
#undef main
#include "qualification_formatter_fixture.h"
static void qualification_bounds(void){
 lv_point_t size;
 lv_text_get_size(&size,lv_label_get_text(body),lv_obj_get_style_text_font(body,0),0,0,200,LV_TEXT_FLAG_NONE);
 assert(size.y<=166);
 lv_text_get_size(&size,lv_label_get_text(footer),lv_obj_get_style_text_font(footer,0),0,0,204,LV_TEXT_FLAG_NONE);
 assert(size.y<=40);
 /* Footer uses CLIP: implicit wrapping in lv_text_get_size is not visibility. */
 const char *line=lv_label_get_text(footer);
 while(*line){const char *end=strchr(line,'\n');size_t bytes=end?(size_t)(end-line):strlen(line);char piece[128];assert(bytes<sizeof piece);memcpy(piece,line,bytes);piece[bytes]=0;
  lv_text_get_size(&size,piece,lv_obj_get_style_text_font(footer,0),0,0,LV_COORD_MAX,LV_TEXT_FLAG_NONE);assert(size.x<=204);
  if(!end)break;
  line=end+1;
 }
 lv_text_get_size(&size,lv_label_get_text(badge),&lv_font_montserrat_14,lv_obj_get_style_text_letter_space(badge,0),0,LV_COORD_MAX,LV_TEXT_FLAG_NONE);
 assert(size.x<=180);
 assert(lv_obj_has_flag(bubble,LV_OBJ_FLAG_HIDDEN));
 assert(!strstr(lv_label_get_text(body),"SECRET"));
}
int main(int argc,char **argv){
 int failed=legacy_snapshot_main(argc,argv);const char*out=argc>1?argv[1]:".";
 static const struct {const char *name,*title,*detail,*hint;bool locked;} cases[]={
  {"idle","USB UNAVAILABLE","No host request.\nNo capture started.\nConnect approved host\nbefore qualification.","UP/DOWN: pages",false},
  {"silent-request","SILENT REQUEST","Mic/ref sent to USB\nSound: none\nUse nonprivate speech.\nOne finite run only.","Hold OK, release, press\nUP/DOWN: pages",false},
  {"audio-request","AUDIO TEST REQUEST","Mic/ref sent to USB\nQuiet sound: max 5%\nUse nonprivate speech.\nSaved mute stays muted.","Hold OK, release, press\nUP/DOWN: pages",false},
  {"armed","PHYSICALLY ARMED","Mic/ref sent to USB\nSound: none\nRelease, then fresh press.\nArm expires in 10 seconds.","Fresh OK: start\nUP/DOWN: pages",false},
  {"running","CAPTURING TO USB","Mic/ref sent to USB\nQuiet sound: max 5%\nFrames 2400 / 2400\nCleanup: pending","Any press: stop\nUP/DOWN: pages",false},
  {"host-loss","HOST LOST","Capture stopped.\nHost heartbeat missing.\nCleanup: checked\nNo automatic retry.","One attempt used\nUP/DOWN: pages",false},
  {"finished","CAPTURE FINISHED","Frames 1000 / 1000\nCleanup: checked\nNot acoustic validation.\nData sent to local host.","One attempt used\nUP/DOWN: pages",false},
  {"cleanup-locked","CLEANUP FAILED","Audio ownership retained.\nNew audio is blocked.\nCleanup not verified.\nScreen off is not power off.","Recovery needed\nUP/DOWN: pages",true}
 };
 for(unsigned i=0;i<sizeof cases/sizeof cases[0];i++){
  pocket_ui_view v={.page=POCKET_DIAGNOSTICS,.qualification_mode=true,.qualification_locked=cases[i].locked,
   .qualification_title=cases[i].title,.qualification_detail=cases[i].detail,.qualification_hint=cases[i].hint,
   .battery_percent=76,.text="SECRET TRANSCRIPT",.reply="SECRET REPLY",.message="SECRET TOKEN",.reader_active=true,.task_result="SECRET READER",.reader_utf8_bytes=13,.reader_has_page=true};
  pocket_ui_render(&v);advance(20);
  assert(!strcmp(lv_label_get_text(badge),cases[i].title));assert(!strcmp(lv_label_get_text(body),cases[i].detail));
  assert(!strcmp(lv_label_get_text(footer),cases[i].hint));
  assert(lv_color_to_u32(lv_obj_get_style_bg_color(badge,0))==lv_color_to_u32(lv_color_hex(cases[i].locked?CORAL:AMBER)));
  qualification_bounds();char name[64];snprintf(name,sizeof name,"qualification-%s",cases[i].name);failed|=save(out,name);
 }
 /* Exact production formatter extracted by the Python harness, no hand copies. */
 static const struct {const char *name;bool tls,pending,confirmed,clean; pocket_qualification_control_phase phase;} actual[]={
  {"usb-max",false,false,false,false,PQC_RUNNING},
  {"usb-armed",false,false,false,false,PQC_PHYSICAL_ARMED},
  {"usb-cleanup",false,false,false,false,PQC_QUARANTINED},
  {"tls-max",true,false,false,false,PQC_RUNNING},
  {"tls-pending",true,true,false,false,PQC_RUNNING},
  {"tls-cleanup",true,false,false,false,PQC_QUARANTINED},
  {"tls-finished",true,false,true,true,PQC_TERMINAL}
 };
 for(unsigned i=0;i<sizeof actual/sizeof actual[0];i++){
  qualification_control.state=(pocket_qualification_control_snapshot){.phase=actual[i].phase,.tls=actual[i].tls,.profile=PQ_AUDIO,.cleanup_verified=actual[i].clean};
  qualification_frames=actual[i].tls?6000:2400;qualification_network_wait=actual[i].pending;qualification_terminal_confirmed=actual[i].confirmed;
  strcpy(qualification_control.tls_config.token,"SECRET TOKEN");strcpy(qualification_control.tls_config.host,"192.168.1.42");
  char heading[64],detail[320],hint[100];pocket_voice_qualification_format(heading,sizeof heading,detail,sizeof detail,hint,sizeof hint);
  pocket_ui_view actual_view={.page=POCKET_DIAGNOSTICS,.qualification_mode=true,.qualification_locked=actual[i].phase==PQC_QUARANTINED,.qualification_title=heading,.qualification_detail=detail,.qualification_hint=hint,.battery_percent=76};
  pocket_ui_render(&actual_view);advance(20);qualification_bounds();
  assert(strstr(lv_label_get_text(body),"No acoustic pass"));assert(!strstr(lv_label_get_text(body),"192.168.1.42"));
  char name[64];snprintf(name,sizeof name,"qualification-%s",actual[i].name);failed|=save(out,name);
 }
 /* No enum coupling for a future separately approved local TLS exercise. */
 pocket_ui_view v={.page=POCKET_DIAGNOSTICS,.qualification_mode=true,.qualification_title="LOCAL TLS TEST",.qualification_detail="Local TLS test\nNo provider calls.\nExplicit test host only.",.qualification_hint="Any press: stop\nUP/DOWN: pages"};
 pocket_ui_render(&v);assert(strstr(lv_label_get_text(body),"No provider"));qualification_bounds();
 v.qualification_title=v.qualification_detail=v.qualification_hint=NULL;pocket_ui_render(&v);qualification_bounds();assert(strstr(lv_label_get_text(body),"No capture started"));
 v.qualification_mode=false;pocket_ui_render(&v);assert(!strstr(lv_label_get_text(badge),"QUALIFICATION"));
 return failed;
}
