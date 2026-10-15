/* Hermes Pocket MIT. Screen layout and animated cartoon cat in pure LVGL. */
#include "pocket_ui.h"
#include "lvgl.h"
#include <stdio.h>
#include <string.h>

enum { BG=0x101C25, CARD=0x1A2C39, ROW=0x223847, LINE=0x3C5968, TEXT=0xF4F1E9, MUTED=0xB9CBD6,
       MINT=0x8FE3CB, CORAL=0xF3A88D, AMBER=0xF6C86B, SKY=0x8CC8F2, SLATE=0x6F8796 };
enum { FUR=0xF5B169, STRIPE=0xE0873F, CREAM=0xFFEBD2, INK=0x3B2A2F, EAR=0xF59BB0,
       NOSE=0xF07C98, BLUSH=0xF7899C, EYE=0x2A2233, MOUTH=0x7A2E3A, BELL=0xFFD45C, WHITE=0xFFFFFF };
typedef enum { CAT_IDLE, CAT_LISTENING, CAT_THINKING, CAT_SPEAKING, CAT_SLEEPY } cat_mood;

/* LVGL's pool is 24 KB, so the dots, battery icon and status rows are each one
 * custom-drawn object rather than a tree of widgets. */
static lv_obj_t *title,*battery,*battery_icon,*dots,*cat,*badge,*rows,*bubble,*heard,*said,*body,*note,*footer,*off_option;
static lv_timer_t *animation;
static cat_mood mood;
static unsigned frame,active_page;
static int battery_level;
typedef struct { const char *name,*value; uint32_t dot; } row_item;
static row_item row_items[3];
static pocket_usage_display usage_items[POCKET_USAGE_PROVIDER_COUNT];
static bool expanded_rows;

static lv_obj_t *shape(lv_obj_t *parent,int x,int y,int width,int height,uint32_t color,int radius) {
 lv_obj_t *obj=lv_obj_create(parent);lv_obj_remove_style_all(obj);lv_obj_set_pos(obj,x,y);lv_obj_set_size(obj,width,height);
 lv_obj_set_style_bg_color(obj,lv_color_hex(color),0);lv_obj_set_style_bg_opa(obj,LV_OPA_COVER,0);
 lv_obj_set_style_radius(obj,radius,0);lv_obj_remove_flag(obj,LV_OBJ_FLAG_SCROLLABLE);return obj;
}
static lv_obj_t *label(lv_obj_t *parent,int x,int y,int width,int height,uint32_t color,lv_label_long_mode_t mode) {
 lv_obj_t *obj=lv_label_create(parent);lv_obj_set_pos(obj,x,y);lv_obj_set_size(obj,width,height);
 lv_label_set_long_mode(obj,mode);lv_obj_set_style_text_color(obj,lv_color_hex(color),0);return obj;
}
static void show(lv_obj_t *obj,bool visible) {
 if(visible)lv_obj_remove_flag(obj,LV_OBJ_FLAG_HIDDEN);else lv_obj_add_flag(obj,LV_OBJ_FLAG_HIDDEN);
}

/* ---- Cat drawing: primitives straight into LVGL's buffer, no image or layer allocation. ---- */
static lv_layer_t *layer;
static int32_t ox,oy;
static void blob(int x1,int y1,int x2,int y2,int radius,uint32_t color,int outline,lv_opa_t opa) {
 lv_draw_rect_dsc_t d;lv_draw_rect_dsc_init(&d);d.radius=radius;d.bg_color=lv_color_hex(color);d.bg_opa=opa;
 if(outline){d.border_width=outline;d.border_color=lv_color_hex(INK);}
 const lv_area_t area={ox+x1,oy+y1,ox+x2,oy+y2};lv_draw_rect(layer,&d,&area);
}
static void tri(int ax,int ay,int bx,int by,int cx,int cy,uint32_t color) {
 lv_draw_triangle_dsc_t d;lv_draw_triangle_dsc_init(&d);d.color=lv_color_hex(color);
 d.p[0].x=ox+ax;d.p[0].y=oy+ay;d.p[1].x=ox+bx;d.p[1].y=oy+by;d.p[2].x=ox+cx;d.p[2].y=oy+cy;
 lv_draw_triangle(layer,&d);
}
static void stroke(int x1,int y1,int x2,int y2,int width,uint32_t color) {
 lv_draw_line_dsc_t d;lv_draw_line_dsc_init(&d);d.color=lv_color_hex(color);d.width=width;d.round_start=1;d.round_end=1;
 d.p1.x=ox+x1;d.p1.y=oy+y1;d.p2.x=ox+x2;d.p2.y=oy+y2;lv_draw_line(layer,&d);
}
static void curve(int cx,int cy,int radius,int start,int end,int width,uint32_t color,lv_opa_t opa) {
 lv_draw_arc_dsc_t d;lv_draw_arc_dsc_init(&d);d.color=lv_color_hex(color);d.width=width;d.rounded=1;d.opa=opa;
 d.center.x=ox+cx;d.center.y=oy+cy;d.radius=radius;d.start_angle=start;d.end_angle=end;lv_draw_arc(layer,&d);
}
static void glyph(int x,int y,const lv_font_t *font,const char *text,lv_opa_t opa) {
 lv_draw_label_dsc_t d;lv_draw_label_dsc_init(&d);d.font=font;d.color=lv_color_hex(MUTED);d.opa=opa;d.text=text;
 const lv_area_t area={ox+x,oy+y,ox+x+24,oy+y+24};lv_draw_label(layer,&d,&area);
}
static void begin(lv_event_t *event);
/* Cat coordinates are symmetric around x=70; M() mirrors a left-side x to the right. */
#define M(x) (140-(x))
static void ear(bool left,int tip_x,int tip_y,int bob) {
 #define S(x) (left?(x):M(x))
 tri(S(tip_x-2),tip_y-4+bob,S(11),44+bob,S(60),12+bob,INK);
 tri(S(tip_x),tip_y+bob,S(16),42+bob,S(55),14+bob,FUR);
 tri(S(tip_x+3),tip_y+5+bob,S(22),31+bob,S(43),14+bob,EAR);
 #undef S
}
static void eye(int cx,int dx,int dy,int b,bool wide) {
 const int grow=wide?2:0,x=cx+dx,y=dy+b;
 blob(x-8-grow,37+y-grow,x+8+grow,57+y+grow,9,EYE,0,LV_OPA_COVER);
 blob(x-5-grow,40+y-grow,x+1,46+y,4,WHITE,0,LV_OPA_COVER);
 blob(x+2,50+y,x+5,53+y,2,WHITE,0,LV_OPA_COVER);
}
static int wave(unsigned period,int amplitude) {
 /* Triangle wave, -amplitude..amplitude, without floating point. */
 const int phase=(int)(frame%period),half=(int)period/2;
 return (phase<half?phase:(int)period-phase)*2*amplitude/half-amplitude;
}
static void draw_cat(lv_event_t *event) {
 begin(event);ox+=20;
 const bool sleepy=mood==CAT_SLEEPY,listening=mood==CAT_LISTENING,speaking=mood==CAT_SPEAKING,thinking=mood==CAT_THINKING;
 const int b=(mood==CAT_IDLE||sleepy)&&(frame/10)%2; /* Slow breathing bob. */

 /* Tail curls up the right side and swishes; still while asleep. */
 const int swish=sleepy?0:wave(speaking?10:24,speaking?14:listening?5:10);
 curve(116,84,15,275+swish,100,10,INK,LV_OPA_COVER);
 curve(116,84,15,275+swish,100,6,FUR,LV_OPA_COVER);
 curve(116,84,15,300+swish,330+swish,6,STRIPE,LV_OPA_COVER);

 /* Loaf body, cream belly and front paws. */
 blob(30,60,110,101,24,FUR,2,LV_OPA_COVER);
 blob(52,76,88,101,16,CREAM,0,LV_OPA_COVER);
 blob(43,89,63,101,8,CREAM,2,LV_OPA_COVER);
 blob(77,89,97,101,8,CREAM,2,LV_OPA_COVER);
 stroke(50,95,50,99,1,INK);stroke(56,95,56,99,1,INK);stroke(84,95,84,99,1,INK);stroke(90,95,90,99,1,INK);

 /* Ears perk up while listening and droop while asleep. */
 const int tip_x=sleepy?20:28,tip_y=listening?3:sleepy?13:6;
 ear(true,tip_x,tip_y,b);ear(false,tip_x,tip_y,b);

 /* Head, tabby stripes, muzzle and blush. */
 blob(17,14+b,123,83+b,34,FUR,2,LV_OPA_COVER);
 blob(67,16+b,73,30+b,3,STRIPE,0,LV_OPA_COVER);
 blob(56,19+b,61,28+b,3,STRIPE,0,LV_OPA_COVER);blob(M(61),19+b,M(56),28+b,3,STRIPE,0,LV_OPA_COVER);
 blob(22,42+b,31,46+b,2,STRIPE,0,LV_OPA_COVER);blob(M(31),42+b,M(22),46+b,2,STRIPE,0,LV_OPA_COVER);
 blob(52,56+b,88,79+b,12,CREAM,0,LV_OPA_COVER);
 blob(25,58+b,41,67+b,5,BLUSH,0,LV_OPA_50);blob(M(41),58+b,M(25),67+b,5,BLUSH,0,LV_OPA_50);

 /* Eyes: blink, glance around, sparkle, squint happily or sleep. */
 if(sleepy){curve(47,45+b,7,20,160,3,INK,LV_OPA_COVER);curve(M(47),45+b,7,20,160,3,INK,LV_OPA_COVER);}
 else if(speaking){curve(47,53+b,7,200,340,3,INK,LV_OPA_COVER);curve(M(47),53+b,7,200,340,3,INK,LV_OPA_COVER);}
 else if(frame%40>=38){stroke(40,48+b,54,48+b,3,INK);stroke(M(54),48+b,M(40),48+b,3,INK);}
 else {
  const unsigned glance=frame%90;
  const int dx=thinking?3:mood!=CAT_IDLE?0:glance>=50&&glance<62?-3:glance>=70&&glance<82?3:0,dy=thinking?-3:0;
  eye(47,dx,dy,b,listening);eye(M(47),dx,dy,b,listening);
 }

 /* Nose, then a cat "w" mouth, an "o" while listening or a chattering open mouth. */
 blob(64,56+b,76,63+b,4,NOSE,1,LV_OPA_COVER);
 if(speaking){
  const int open=frame%4<2?11:6;
  blob(63,65+b,77,65+open+b,7,MOUTH,2,LV_OPA_COVER);blob(66,62+open+b,74,64+open+b,2,NOSE,0,LV_OPA_COVER);
 }
 else if(listening)blob(66,65+b,74,73+b,4,MOUTH,2,LV_OPA_COVER);
 else {curve(66,64+b,4,0,180,2,INK,LV_OPA_COVER);curve(74,64+b,4,0,180,2,INK,LV_OPA_COVER);}

 /* Whiskers reach past the cheeks. */
 stroke(32,63+b,8,58+b,2,INK);stroke(32,68+b,8,71+b,2,INK);
 stroke(M(32),63+b,M(8),58+b,2,INK);stroke(M(32),68+b,M(8),71+b,2,INK);

 /* Mint collar and a gold bell. */
 blob(48,80,92,86,3,MINT,1,LV_OPA_COVER);
 blob(65,84,75,94,5,BELL,1,LV_OPA_COVER);stroke(70,90,70,92,1,INK);

 /* Mood effects in the free space beside the head. */
 if(listening||speaking){
  const uint32_t color=listening?CORAL:SKY;const int y=listening?32:64;
  for(int ring=0;ring<2;ring++){
   const lv_opa_t opa=(frame+ring*3)%6<3?LV_OPA_COVER:LV_OPA_30;
   curve(126,y,7+ring*6,315,45,3,color,opa);curve(14,y,7+ring*6,135,225,3,color,opa);
  }
 }
 if(thinking){
  blob(126,24,131,29,3,TEXT,0,LV_OPA_COVER);blob(132,13,139,20,4,TEXT,0,LV_OPA_COVER);
  blob(136,0,159,14,7,TEXT,0,LV_OPA_COVER);
  for(int dot=0;dot<3;dot++){
   const int x=141+dot*6;blob(x,6,x+3,9,2,frame/3%3==(unsigned)dot?INK:SLATE,0,LV_OPA_COVER);
  }
 }
 if(sleepy){
  const int drift=(int)(frame%24)/3;
  glyph(124,30-drift,&lv_font_montserrat_14,"z",drift<6?LV_OPA_COVER:LV_OPA_50);
  glyph(134,12-drift/2,&lv_font_montserrat_20,"Z",drift<6?LV_OPA_70:LV_OPA_30);
 }
}
static void animate(lv_timer_t *timer) {
 (void)timer;
 if(lv_obj_has_flag(cat,LV_OBJ_FLAG_HIDDEN))return;
 frame++;lv_obj_invalidate(cat);
}

/* ---- Custom-drawn widgets ---- */
static void begin(lv_event_t *event) {
 /* Draw straight into LVGL's buffer at the target's position. */
 layer=lv_event_get_layer(event);
 lv_area_t area;lv_obj_get_coords(lv_event_get_target(event),&area);ox=area.x1;oy=area.y1;
}
static void text_at(int x1,int y1,int x2,const lv_font_t *font,const char *text,uint32_t color,lv_text_align_t align) {
 lv_draw_label_dsc_t d;lv_draw_label_dsc_init(&d);d.font=font;d.color=lv_color_hex(color);
 d.text=text;d.align=align;
 const lv_area_t area={ox+x1,oy+y1,ox+x2,oy+y1+19};lv_draw_label(layer,&d,&area);
}
static void usage_text(int x1,int y1,int x2,const char *text,uint32_t color,lv_text_align_t align) {
 /* Keep optional long source labels on one line without drawing over the next
  * provider or the page controls. All formatter output is bounded ASCII. */
 char clipped[64];snprintf(clipped,sizeof clipped,"%s",text);
 lv_point_t size;const lv_font_t *font=&lv_font_montserrat_14;
 lv_text_get_size(&size,clipped,font,0,0,LV_COORD_MAX,LV_TEXT_FLAG_NONE);
 if(size.x>x2-x1+1){
  size_t length=strlen(clipped);
  while(length){
   clipped[--length]='\0';
   char trial[68];snprintf(trial,sizeof trial,"%s...",clipped);
   lv_text_get_size(&size,trial,font,0,0,LV_COORD_MAX,LV_TEXT_FLAG_NONE);
   if(size.x<=x2-x1+1){snprintf(clipped,sizeof clipped,"%.63s",trial);break;}
  }
 }
 /* Deferred draw tasks must own the clipped stack text until rendering ends. */
 lv_draw_label_dsc_t d;lv_draw_label_dsc_init(&d);d.font=font;d.color=lv_color_hex(color);
 d.text=clipped;d.text_local=1;d.align=align;
 const lv_area_t area={ox+x1,oy+y1,ox+x2,oy+y1+19};lv_draw_label(layer,&d,&area);
}
static void draw_dots(lv_event_t *event) {
 begin(event);
 for(unsigned i=0,x=120-(14+6*(POCKET_PAGE_COUNT-1)+8*(POCKET_PAGE_COUNT-1))/2;i<POCKET_PAGE_COUNT;i++){
  const bool active=i==active_page;
  blob((int)x,0,(int)x+(active?13:5),5,3,active?MINT:LINE,0,LV_OPA_COVER);x+=(active?14:6)+8;
 }
}
static void draw_battery(lv_event_t *event) {
 begin(event);
 lv_draw_rect_dsc_t d;lv_draw_rect_dsc_init(&d);d.radius=3;d.bg_opa=LV_OPA_TRANSP;
 d.border_width=2;d.border_color=lv_color_hex(MUTED);
 const lv_area_t outline={ox,oy,ox+19,oy+10};lv_draw_rect(layer,&d,&outline);
 blob(20,3,22,7,1,MUTED,0,LV_OPA_COVER);
 /* Fill to the gauge's SOC; nothing at all when there is no trustworthy reading. */
 if(battery_level>=0)blob(3,3,3+(battery_level>=100?13:battery_level*13/100),7,1,battery_level<=15?CORAL:MINT,0,LV_OPA_COVER);
}
static void draw_rows(lv_event_t *event) {
 begin(event);
 for(int i=0;i<3;i++){
  if(expanded_rows){
   const int y=i*56;
   blob(0,y,199,y+52,12,ROW,0,LV_OPA_COVER);
   blob(8,y+8,13,y+13,3,row_items[i].dot,0,LV_OPA_COVER);
   usage_text(20,y+2,80,row_items[i].name,TEXT,LV_TEXT_ALIGN_LEFT);
   usage_text(82,y+2,189,usage_items[i].value,TEXT,LV_TEXT_ALIGN_RIGHT);
   usage_text(10,y+19,189,usage_items[i].detail,MUTED,LV_TEXT_ALIGN_LEFT);
   const pocket_usage_availability state=usage_items[i].availability;
   usage_text(10,y+36,189,usage_items[i].freshness,
              state==POCKET_USAGE_FAILED?CORAL:state==POCKET_USAGE_STALE||state==POCKET_USAGE_PARTIAL?AMBER:MUTED,LV_TEXT_ALIGN_LEFT);
   continue;
  }
  const int y=i*26;
  blob(0,y,199,y+23,12,ROW,0,LV_OPA_COVER);
  blob(10,y+7,19,y+16,5,row_items[i].dot,0,LV_OPA_COVER);
  text_at(28,y+2,110,&lv_font_source_han_sans_sc_16_cjk,row_items[i].name,TEXT,LV_TEXT_ALIGN_LEFT);
  text_at(64,y+4,189,&lv_font_montserrat_14,row_items[i].value,MUTED,LV_TEXT_ALIGN_RIGHT);
 }
}
static lv_obj_t *canvas(lv_obj_t *parent,int x,int y,int width,int height,lv_event_cb_t draw) {
 lv_obj_t *obj=lv_obj_create(parent);lv_obj_remove_style_all(obj);lv_obj_set_pos(obj,x,y);lv_obj_set_size(obj,width,height);
 lv_obj_remove_flag(obj,LV_OBJ_FLAG_SCROLLABLE|LV_OBJ_FLAG_CLICKABLE);
 lv_obj_add_event_cb(obj,draw,LV_EVENT_DRAW_MAIN,NULL);return obj;
}

/* ---- Screen ---- */
static void set_badge(const char *text,uint32_t color,int y) {
 lv_label_set_text(badge,text);lv_obj_set_style_bg_color(badge,lv_color_hex(color),0);
 lv_obj_align(badge,LV_ALIGN_TOP_MID,0,y);
}
static void set_row(unsigned i,const char *name,const char *value,uint32_t dot) {
 row_items[i]=(row_item){name,value,dot};
}
static void show_rows(int y) {expanded_rows=false;show(rows,true);lv_obj_set_y(rows,y);lv_obj_set_height(rows,76);lv_obj_invalidate(rows);}
static void show_usage_rows(const pocket_ui_view *view) {
 static const char *providers[]={"Codex","Claude","Ollama"};
 for(unsigned i=0;i<POCKET_USAGE_PROVIDER_COUNT;i++){
  pocket_usage_format(view->usage?&view->usage->providers[i]:NULL,view->usage_window,view->now,&usage_items[i]);
  const pocket_usage_availability state=usage_items[i].availability;
  const uint32_t dot=state==POCKET_USAGE_AVAILABLE?MINT:state==POCKET_USAGE_FAILED?CORAL:
                     state==POCKET_USAGE_STALE||state==POCKET_USAGE_PARTIAL?AMBER:SLATE;
  set_row(i,providers[i],usage_items[i].value,dot);
 }
 expanded_rows=true;show(rows,true);lv_obj_set_y(rows,94);lv_obj_set_height(rows,168);lv_obj_invalidate(rows);
}
void pocket_ui_create(void) {
 lv_obj_t *screen=lv_obj_create(NULL);
 lv_obj_set_style_bg_color(screen,lv_color_hex(BG),0);
 lv_obj_set_style_text_color(screen,lv_color_hex(TEXT),0);
 lv_obj_set_style_text_font(screen,&lv_font_source_han_sans_sc_16_cjk,0);
 lv_obj_remove_flag(screen,LV_OBJ_FLAG_SCROLLABLE);
 shape(screen,10,52,220,216,CARD,18);

 title=label(screen,18,16,136,20,MINT,LV_LABEL_LONG_DOT);
 /* Battery icon fills to the gauge's SOC; the percentage stays as text. */
 battery_icon=canvas(screen,158,20,23,11,draw_battery);
 battery=label(screen,184,16,42,20,MUTED,LV_LABEL_LONG_CLIP);lv_obj_set_style_text_align(battery,LV_TEXT_ALIGN_RIGHT,0);
 dots=canvas(screen,0,41,240,6,draw_dots);
 cat=canvas(screen,30,50,180,102,draw_cat);

 badge=lv_label_create(screen);lv_obj_set_style_text_font(badge,&lv_font_montserrat_14,0);
 lv_obj_set_style_text_color(badge,lv_color_hex(BG),0);lv_obj_set_style_text_letter_space(badge,1,0);
 lv_obj_set_style_bg_opa(badge,LV_OPA_COVER,0);lv_obj_set_style_radius(badge,11,0);
 lv_obj_set_style_pad_hor(badge,12,0);lv_obj_set_style_pad_ver(badge,4,0);
 lv_obj_set_style_max_width(badge,204,0);lv_label_set_long_mode(badge,LV_LABEL_LONG_CLIP);

 rows=canvas(screen,20,0,200,76,draw_rows);
 bubble=shape(screen,18,182,204,80,ROW,14);
 lv_obj_set_style_pad_hor(bubble,10,0);lv_obj_set_style_pad_ver(bubble,6,0);
 lv_obj_set_flex_flow(bubble,LV_FLEX_FLOW_COLUMN);
 heard=lv_label_create(bubble);lv_obj_set_size(heard,lv_pct(100),20);lv_label_set_long_mode(heard,LV_LABEL_LONG_DOT);
 lv_obj_set_style_text_color(heard,lv_color_hex(SKY),0);
 said=lv_label_create(bubble);lv_obj_set_width(said,lv_pct(100));lv_obj_set_flex_grow(said,1);
 lv_label_set_long_mode(said,LV_LABEL_LONG_DOT);

 body=label(screen,20,0,200,92,TEXT,LV_LABEL_LONG_WRAP);
 note=label(screen,20,196,200,44,MUTED,LV_LABEL_LONG_WRAP);
 off_option=shape(screen,30,224,180,34,MINT,17);
 lv_obj_t *off_label=lv_label_create(off_option);lv_label_set_text(off_label,"OK: Screen off");lv_obj_center(off_label);
 lv_obj_set_style_text_color(off_label,lv_color_hex(BG),0);
 footer=label(screen,18,273,204,40,MUTED,LV_LABEL_LONG_CLIP);
 /* The 16px CJK font has a 20px line height: reserve two complete lines. */
 lv_obj_set_style_text_line_space(footer,0,0);
 animation=lv_timer_create(animate,120,NULL);
 lv_screen_load(screen);
}
void pocket_ui_set_awake(bool awake) {
 if(awake)lv_timer_resume(animation);else lv_timer_pause(animation);
}
void pocket_ui_render(const pocket_ui_view *v) {
 static const char *names[]={"HOME","CHAT","USAGE","DIAG","TASKS","TOOLS","AUDIO","ALERTS","SCREEN"};
 lv_label_set_text_fmt(title,"%s / %s",names[v->page],v->target==0?"WIN":"LINUX");
 const int percent=v->battery_percent<0?-1:v->battery_percent;
 if(percent>=0)lv_label_set_text_fmt(battery,"%d%%",percent);else lv_label_set_text(battery,"N/A");
 lv_obj_set_style_text_color(battery,lv_color_hex(percent>=0&&percent<=15?CORAL:MUTED),0);
 if(percent!=battery_level){battery_level=percent;lv_obj_invalidate(battery_icon);}
 const unsigned page=v->setup_active?POCKET_PAGE_COUNT:(unsigned)v->page;
 if(page!=active_page){active_page=page;lv_obj_invalidate(dots);}

 /* Voice activity wins so a press always gets a reaction; otherwise the cat
  * sleeps whenever Hermes is offline, on every page that shows it. */
 const bool active=v->page==POCKET_CHAT&&v->voice_here&&(v->busy||v->recording||v->playing);
 cat_mood next=CAT_IDLE;
 if(active)next=v->recording?CAT_LISTENING:v->playing?CAT_SPEAKING:CAT_THINKING;
 else if(v->page==POCKET_SCREEN_OFF||!v->online)next=CAT_SLEEPY;
 if(next!=mood){mood=next;frame=0;lv_obj_invalidate(cat);}
 show(cat,!v->setup_active&&(v->page==POCKET_STATUS||v->page==POCKET_CHAT||v->page==POCKET_SCREEN_OFF));
 lv_obj_set_style_text_font(body,!v->setup_active&&(v->page==POCKET_DIAGNOSTICS||v->page==POCKET_TOOLS||v->page==POCKET_AUDIO||v->page==POCKET_ALERTS)?&lv_font_montserrat_14:&lv_font_source_han_sans_sc_16_cjk,0);
 lv_label_set_long_mode(body,v->page==POCKET_TASKS&&!v->setup_active?LV_LABEL_LONG_DOT:LV_LABEL_LONG_WRAP);
 show(rows,false);show(bubble,false);show(body,false);show(note,false);show(off_option,false);

 if(v->setup_active){
  lv_label_set_text(title,"PHONE SETUP");set_badge("SETUP / 5 MIN",AMBER,60);
  show(body,true);lv_obj_set_pos(body,20,92);lv_obj_set_height(body,170);
  lv_label_set_text_fmt(body,"1. Join this Wi-Fi\n%s\nPassword: %s\n\n2. Open on your phone\nhttp://192.168.4.1",v->ssid,v->password);
  lv_label_set_text(footer,"Finish setup on your phone");return;
 }
 if(v->page==POCKET_CHAT){
  if(!active&&!v->online)set_badge("HERMES OFFLINE",SLATE,154);
  else set_badge(!v->voice_here?"READY TO CHAT":v->recording?"LISTENING":v->playing?"SPEAKING":v->busy?"THINKING":"LET'S TALK",
                 !v->voice_here?MINT:v->recording?CORAL:v->playing?SKY:v->busy?AMBER:MINT,154);
  show(bubble,true);
  const bool has_text=v->voice_here&&v->text&&v->text[0];
  show(heard,has_text);if(has_text)lv_label_set_text_fmt(heard,"You: %.100s",v->text);
  if(!v->voice_here)lv_label_set_text(said,"Your private companion\nHold OK to start talking.");
  /* The reply takes priority; status (progress or error) shows until one arrives. */
  else if(v->reply&&v->reply[0])lv_label_set_text_fmt(said,"%.220s",v->reply);
  else lv_label_set_text(said,v->message?v->message:"");
  lv_label_set_text(footer,v->busy?"OK: stop\nUP/DOWN: pages":"Hold OK: talk\nUP/DOWN: pages");return;
 }
 if(v->page==POCKET_STATUS){
  set_badge(v->alert_pending?"USAGE ALERT":v->online?"HERMES ONLINE":"HERMES POCKET",v->alert_pending?AMBER:v->online?MINT:SLATE,154);
  set_row(0,"Wi-Fi",v->wifi_connected?"Connected":"Offline",v->wifi_connected?MINT:CORAL);
  set_row(1,"Hermes",v->online?"Available":"Offline",v->online?MINT:CORAL);
  set_row(2,"Voice",v->speech_ready?"Ready":"Needs setup",v->speech_ready?MINT:AMBER);show_rows(182);
  lv_label_set_text(footer,v->target_save_failed?"Target not saved\nUP/DOWN: pages":"OK: agent  Hold: setup\nUP/DOWN: pages");return;
 }
 if(v->page==POCKET_DIAGNOSTICS){
  char status[40],detail[240];
  pocket_diagnostics_format(v->diagnostics,v->uptime_ms,v->busy,status,sizeof(status),detail,sizeof(detail));
  set_badge(status,v->diagnostics&&v->diagnostics->backend_available?SKY:AMBER,60);
  show(body,true);lv_obj_set_pos(body,20,96);lv_obj_set_height(body,166);lv_label_set_text(body,detail);
  lv_label_set_text(footer,"Last observed status\nUP/DOWN: pages");return;
 }
 if(v->page==POCKET_TASKS){
  set_badge(v->task_count&&v->task_phase?v->task_phase:"TASK LIST",v->task_approval||v->task_unknown?AMBER:SKY,60);
  show(note,true);lv_obj_set_pos(note,20,94);lv_obj_set_height(note,36);lv_obj_set_style_text_font(note,&lv_font_montserrat_14,0);
  if(v->task_count)lv_label_set_text_fmt(note,"Task %u/%u  %s\n%s",v->task_selected+1,v->task_count,v->task_status?v->task_status:"Not fetched",v->task_truncated?"Result truncated":"");
  else lv_label_set_text(note,v->task_status?v->task_status:"Not fetched");
  show(body,true);lv_obj_set_pos(body,20,134);lv_obj_set_height(body,124);
  if(v->task_has_result)lv_label_set_text(body,v->task_result&&v->task_result[0]?v->task_result:"Empty result");
  else lv_label_set_text(body,v->task_approval?"Open Hermes to approve.":v->task_unknown?"Outcome unknown.\nCheck Hermes.":v->task_count?"No result yet.":"Hold OK to refresh tasks.");
  lv_label_set_text(footer,v->busy?"OK: stop locally\nUP/DOWN: pages":"OK: next  Hold: reload\nUP/DOWN: pages");return;
 }
 if(v->page==POCKET_TOOLS){
  static const char *tools[]={"STATUS","USAGE","TASKS","TODO UNAVAILABLE"};
  const unsigned tool=v->tool<POCKET_TOOL_COUNT?(unsigned)v->tool:POCKET_TOOL_TODO_UNAVAILABLE;
  set_badge(tools[tool],tool==POCKET_TOOL_TODO_UNAVAILABLE?SLATE:SKY,60);
  show(body,true);lv_obj_set_pos(body,20,96);lv_obj_set_height(body,166);
  static const char *details[]={"Read-only connection status\n\nHold OK to queue a refresh.\nDetails appear on Diag.","Account Usage\n\nHold OK to request fresh data.\nValues appear on Usage.","Read-only task list\n\nHold OK to queue a refresh.\nResults appear on Tasks.","Todo is not available\nfor this bridge."};
  lv_label_set_text(body,details[tool]);
  lv_label_set_text(footer,v->busy?"OK: stop locally\nUP/DOWN: pages":"OK: choose  Hold: run\nUP/DOWN: pages");return;
 }
 if(v->page==POCKET_AUDIO){
  set_badge(v->audio_edit?"EDIT VOLUME":"SPEAKER VOLUME",v->audio_edit?MINT:SKY,60);
  show(body,true);lv_obj_set_pos(body,20,96);lv_obj_set_height(body,166);
  lv_label_set_text_fmt(body,"Volume: %u%%\n%s\n\n%s",v->volume,v->volume?"Speaker output":"Muted",v->audio_edit?"UP/DOWN: change by 5%\nOK: finish editing":"OK: edit volume");
  lv_label_set_text(footer,v->busy?"OK: stop locally\nUP/DOWN: volume/pages":v->audio_edit?"OK: finish editing\nUP/DOWN: volume":"OK: edit\nUP/DOWN: pages");return;
 }
 if(v->page==POCKET_ALERTS){
  set_badge("USAGE REMINDERS",SKY,60);show(body,true);lv_obj_set_pos(body,20,96);lv_obj_set_height(body,166);
  if(v->alert_threshold)lv_label_set_text_fmt(body,"Used limit: %u%% used\nRemaining floor: %u%% left\nUSD floor: %s\n\n%s",v->alert_threshold,100-v->alert_threshold,v->usd_floor_enabled?"$1.00 USD":"off",v->alert_note?v->alert_note:"No reminder yet.");
  else lv_label_set_text_fmt(body,"Percent alerts: off\nUSD floor: %s\n\n%s",v->usd_floor_enabled?"$1.00 USD":"off",v->alert_note?v->alert_note:"No reminder yet.");
  lv_label_set_text(footer,v->busy?"OK: stop locally\nUP/DOWN: pages":"OK: limit  Hold: USD\nUP/DOWN: pages");return;
 }
 if(v->page==POCKET_SCREEN_OFF){
  set_badge("SCREEN STANDBY",SLATE,154);
  show(body,true);lv_obj_set_pos(body,20,182);lv_obj_set_height(body,40);
  lv_label_set_text(body,"Wi-Fi stays on.\nAny button wakes it.");
  show(off_option,true);
  lv_label_set_text(footer,"OK: screen off\nUP/DOWN: pages");return;
 }
 set_badge(v->alert_pending?"USAGE ALERT":"ACCOUNT OVERVIEW",v->alert_pending?AMBER:SKY,60);
 show_usage_rows(v);
 lv_label_set_text(footer,"OK: next window\nUP/DOWN: pages");
}
