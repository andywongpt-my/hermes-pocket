/* Real LVGL screens, followed by the complete legacy UI regression fixture. */
#define main legacy_snapshot_main
#include "ui_snapshot.c"
#undef main
static const char *live_text(void){return lv_label_get_text(lv_obj_has_flag(bubble,LV_OBJ_FLAG_HIDDEN)?body:said);}
static void live_bounds(void){
 lv_point_t size;
 lv_obj_t *content=lv_obj_has_flag(bubble,LV_OBJ_FLAG_HIDDEN)?body:said;
 bool compact=content==said;
 lv_text_get_size(&size,lv_label_get_text(content),lv_obj_get_style_text_font(content,0),0,0,compact?184:200,LV_TEXT_FLAG_NONE);
 assert(size.y<=(compact?60:166));
 lv_text_get_size(&size,lv_label_get_text(footer),lv_obj_get_style_text_font(footer,0),0,0,204,LV_TEXT_FLAG_NONE);
 assert(size.y<=40);
 assert(!strstr(live_text(),"OLD TRANSCRIPT"));
 assert(!strstr(live_text(),"OLD REPLY"));
}
int main(int argc,char **argv){
 int failed=legacy_snapshot_main(argc,argv);const char*out=argc>1?argv[1]:".";
 pocket_ui_view v={.page=POCKET_CHAT,.live_mode=true,.voice_here=true,.battery_percent=76,
  .text="OLD TRANSCRIPT",.reply="OLD REPLY",.message="No session started."};
 pocket_ui_render(&v);assert(!strcmp(lv_label_get_text(badge),"LIVE NOT READY"));
 assert(strstr(live_text(),"validation")&&strstr(live_text(),"No session started"));live_bounds();failed|=save(out,"live-unavailable");
 v.live_available=true;v.message="";pocket_ui_render(&v);assert(strstr(lv_label_get_text(footer),"OK: start GPT Live"));live_bounds();failed|=save(out,"live-ready");
 v.busy=true;v.message="Live session active";
 pocket_ui_render(&v);assert(!strcmp(lv_label_get_text(badge),"GPT LIVE"));assert(!strcmp(live_text(),v.message));assert(strstr(lv_label_get_text(footer),"Any press: stop"));assert(!lv_obj_has_flag(cat,LV_OBJ_FLAG_HIDDEN));assert(!lv_obj_has_flag(bubble,LV_OBJ_FLAG_HIDDEN));live_bounds();failed|=save(out,"live-active");
 v.busy=false;v.message="Local session ended. Hermes work may still be pending; check its task status.";
 pocket_ui_render(&v);assert(!strcmp(live_text(),v.message));live_bounds();failed|=save(out,"live-backend-pending");
 v.live_cleanup_pending=true;v.busy=true;v.message="Audio cleanup is incomplete. Ownership is retained; new audio sessions are blocked.";
 pocket_ui_render(&v);assert(!strcmp(lv_label_get_text(badge),"AUDIO LOCKED"));assert(strstr(lv_label_get_text(footer),"Recovery needed"));live_bounds();failed|=save(out,"live-cleanup-pending");
 v.voice_here=false;pocket_ui_render(&v);assert(!strstr(live_text(),"Ownership is retained"));live_bounds();failed|=save(out,"live-other-target-lock");
 v.live_cleanup_pending=false;v.busy=false;pocket_ui_render(&v);assert(!strstr(live_text(),"Ownership is retained"));live_bounds();failed|=save(out,"live-other-target-ready");
 v.page=POCKET_MODELS;v.voice_here=true;v.model_candidate=2;v.model_choice=1;
 pocket_ui_render(&v);assert(strstr(live_text(),"configured default"));assert(strstr(live_text(),"not applied"));assert(!strstr(live_text(),"Claude Opus"));assert(!strstr(lv_label_get_text(footer),"Hold: use"));live_bounds();failed|=save(out,"live-models-read-only");
 /* Round trip returns the original Chat path when mode is disabled. */
 v.live_mode=false;v.page=POCKET_CHAT;v.voice_here=true;pocket_ui_render(&v);
 assert(!lv_obj_has_flag(bubble,LV_OBJ_FLAG_HIDDEN));assert(strstr(lv_label_get_text(said),"OLD REPLY"));
 return failed;
}
