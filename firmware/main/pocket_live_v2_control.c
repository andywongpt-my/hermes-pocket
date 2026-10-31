#include "pocket_live_v2_control.h"
#include "cJSON.h"
#include <string.h>

static bool text(const cJSON *v,const char *s) { return cJSON_IsString(v)&&!strcmp(v->valuestring,s); }
static bool flat(const char *p,size_t n) {
    unsigned depth=0; bool string=false;
    for(size_t i=0;i<n;i++) {
        char c=p[i]; if(!c)return false;
        if(string) {
            if(c=='\\')return false; /* Canonical controls contain no escapes. */
            else if(c=='"')string=false;
        } else if(c=='"')string=true;
        else if(c=='['||c==']')return false;
        else if(c>='0'&&c<='9') {
            while(i+1<n&&p[i+1]>='0'&&p[i+1]<='9')i++;
            if(i+1<n&&(p[i+1]=='.'||p[i+1]=='e'||p[i+1]=='E'))return false;
        }
        else if(c=='{') { if(++depth>1)return false; }
        else if(c=='}') { if(depth!=1)return false; depth--; }
    }
    return !string && depth==0;
}
static bool keys(const cJSON *v,const char *const *expected,unsigned count) {
    if(!cJSON_IsObject(v))return false;
    unsigned found=0;
    const cJSON *item=NULL;
    cJSON_ArrayForEach(item,v) {
        unsigned i=0;
        for(;i<count;i++)if(item->string&&!strcmp(item->string,expected[i]))break;
        if(i==count || (found&(1u<<i)))return false;
        found|=1u<<i;
    }
    return found==((1u<<count)-1);
}
bool pocket_live_v2_control(const char *json,size_t bytes,pocket_live_worker_event *out) {
    if(!json||!out||bytes==0||bytes>1024||!flat(json,bytes))return false;
    char copy[1025];memcpy(copy,json,bytes);copy[bytes]=0;
    cJSON *root=cJSON_ParseWithLengthOpts(copy,bytes+1,NULL,true);
    if(!root)return false;
    pocket_live_worker_event e={0};bool valid=false;
    const cJSON *type=cJSON_GetObjectItemCaseSensitive(root,"type");
    if(text(type,"ready")) {
        const char *const fields[]={"type","version","rate","reference"};
        const cJSON *version=cJSON_GetObjectItemCaseSensitive(root,"version");
        const cJSON *rate=cJSON_GetObjectItemCaseSensitive(root,"rate");
        valid=keys(root,fields,4)&&text(cJSON_GetObjectItemCaseSensitive(root,"reference"),"es8311_dac_right")&&cJSON_IsNumber(version)&&version->valuedouble==2&&
            cJSON_IsNumber(rate)&&rate->valuedouble==24000;
        e.kind=PLW_READY;
    } else if(text(type,"closed")) {
        const char *const fields[]={"type","finalized","reason","backend_pending","playback_pending","actual_playout"};
        const cJSON *final=cJSON_GetObjectItemCaseSensitive(root,"finalized");
        const cJSON *back=cJSON_GetObjectItemCaseSensitive(root,"backend_pending");
        const cJSON *play=cJSON_GetObjectItemCaseSensitive(root,"playback_pending");
        const cJSON *reason=cJSON_GetObjectItemCaseSensitive(root,"reason");
        const char *const reasons[]={"stopped","server_closed","microphone_ended","session_limit","stopped_before_connect","stopped_before_start","stopped_before_ready"};
        bool known=false;
        for(unsigned i=0;i<sizeof reasons/sizeof reasons[0];i++)if(text(reason,reasons[i]))known=true;
        valid=keys(root,fields,6)&&text(cJSON_GetObjectItemCaseSensitive(root,"actual_playout"),"unknown")&&known&&cJSON_IsBool(final)&&cJSON_IsBool(back)&&cJSON_IsBool(play);
        e.kind=PLW_CLOSED;e.finalized=cJSON_IsTrue(final);e.backend_pending=cJSON_IsTrue(back);e.playback_pending=cJSON_IsTrue(play);
    } else if(text(type,"error")) {
        const char *const fields[]={"type","code"};
        valid=keys(root,fields,2)&&text(cJSON_GetObjectItemCaseSensitive(root,"code"),"session_unconfirmed");
        e.kind=PLW_ERROR;
    }
    cJSON_Delete(root);
    if(valid)*out=e;
    return valid;
}
