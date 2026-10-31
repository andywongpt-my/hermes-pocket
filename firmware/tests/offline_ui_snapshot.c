/* Real LVGL rendering plus all original UI regression assertions. */
#define main legacy_snapshot_main
#include "ui_snapshot.c"
#undef main
int main(int argc,char **argv){
 int failed=legacy_snapshot_main(argc,argv);
 const char *out=argc>1?argv[1]:".";
 const pocket_offline_diagnostic_phase phases[]={PDIAG_IDLE,PDIAG_ARMED,PDIAG_RUNNING,PDIAG_DONE,PDIAG_CLEANUP_FAILED};
 const char *names[]={"offline-ready","offline-armed","offline-running","offline-done","offline-cleanup-failed"};
 for(unsigned i=0;i<5;i++){
  pocket_offline_diagnostic_snapshot s={.phase=phases[i],.frames=i?1000:0,.samples=240000,
   .rms={32768,32768},.peak={32768,32768},.clips={240000,240000},.restore_ok=i==3,.cleanup_attempted=i==3||i==4};
  pocket_ui_view v={.page=POCKET_DIAGNOSTICS,.offline_diagnostic=&s,.battery_percent=76};
  pocket_ui_render(&v);advance(20);
  lv_point_t size;
  lv_text_get_size(&size,lv_label_get_text(body),&lv_font_montserrat_14,0,0,200,LV_TEXT_FLAG_NONE);
  assert(size.y<=166);
  lv_text_get_size(&size,lv_label_get_text(footer),lv_obj_get_style_text_font(footer,0),0,0,204,LV_TEXT_FLAG_NONE);
  assert(size.y<=44);
  failed|=save(out,names[i]);
 }
 return failed;
}
