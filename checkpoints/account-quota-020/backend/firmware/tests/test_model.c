#include "pocket_model.h"
#include <assert.h>
#include <stdio.h>
int main(void) {
 pocket_model m; pocket_init(&m);
 assert(m.page==POCKET_STATUS && m.target==0 && !m.connected[0]);
 assert(pocket_press(&m,POCKET_OK)==POCKET_NONE);
 pocket_connection(&m,0,true);
 assert(pocket_press(&m,POCKET_DOWN)==POCKET_NONE && m.page==POCKET_CHAT);
 assert(pocket_press(&m,POCKET_OK)==POCKET_START_RECORDING);
 assert(pocket_press(&m,POCKET_OK)==POCKET_NONE);
 assert(!pocket_select(&m,1)); /* recording cannot change destinations */
 assert(pocket_release(&m,POCKET_OK)==POCKET_SEND_AUDIO);
 assert(m.recording_target==0 && m.submitting);
 assert(pocket_select(&m,1)); /* in-flight request retains original target */
 pocket_submitted(&m,0); assert(!m.submitting);
 assert(!pocket_update(&m,0,0,0,POCKET_RUNNING));
 assert(pocket_update(&m,0,0,1,POCKET_RUNNING));
 assert(!pocket_update(&m,0,0,1,POCKET_DONE));
 assert(pocket_update(&m,1,0,1,POCKET_QUEUED));
 assert(m.task[0]==POCKET_RUNNING && m.task[1]==POCKET_QUEUED);
 assert(pocket_select(&m,0));
 assert(pocket_press(&m,POCKET_OK)==POCKET_START_RECORDING);
 pocket_connection(&m,0,false);
 assert(!m.recording && pocket_release(&m,POCKET_OK)==POCKET_NONE);
 pocket_connection(&m,0,true);m.playing=true;
 assert(pocket_press(&m,POCKET_OK)==POCKET_STOP_PLAYBACK);
 assert(m.task[0]==POCKET_RUNNING);
 assert(!pocket_select(&m,2));
 assert(pocket_update(&m,0,0,2,POCKET_DONE));
 assert(!pocket_update(&m,0,0,3,POCKET_RUNNING));
 pocket_new_task(&m,0);assert(!pocket_update(&m,0,0,99,POCKET_DONE));assert(pocket_update(&m,0,1,1,POCKET_QUEUED));
 for(int i=0;i<1000;i++) { pocket_press(&m,POCKET_DOWN); assert(m.page<POCKET_PAGE_COUNT); }
 puts("pocket model: PASS"); return 0;
}
