/* MIT. Allocation-free prefixes of valid UTF8 Unicode scalars. */
#pragma once
#include <stddef.h>
#include <string.h>

static inline size_t pocket_utf8_prefix(const char *text,size_t limit){
 if(!text)return 0;
 const unsigned char *bytes=(const unsigned char *)text;size_t i=0;
 while(i<limit && bytes[i]){
  const unsigned char c=bytes[i];size_t width;
  if(c<0x80)width=1;
  else if(c>=0xc2 && c<=0xdf)width=2;
  else if(c>=0xe0 && c<=0xef)width=3;
  else if(c>=0xf0 && c<=0xf4)width=4;
  else break;
  if(width>limit-i)break;
  size_t j=1;for(;j<width;j++)if((bytes[i+j]&0xc0)!=0x80)break;
  if(j!=width)break;
  if((c==0xe0 && bytes[i+1]<0xa0) || (c==0xed && bytes[i+1]>=0xa0) ||
     (c==0xf0 && bytes[i+1]<0x90) || (c==0xf4 && bytes[i+1]>=0x90))break;
  i+=width;
 }
 return i;
}
static inline size_t pocket_utf8_copy(char *out,size_t capacity,const char *text,size_t limit){
 if(!out || !capacity)return 0;
 if(limit>capacity-1)limit=capacity-1;
 const size_t bytes=pocket_utf8_prefix(text,limit);
 if(bytes)memmove(out,text,bytes);
 out[bytes]=0;return bytes;
}
