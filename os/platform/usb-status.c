/* USB ownership screen: no player, configuration, audio, network or card I/O. */
#define _POSIX_C_SOURCE 200809L
#include <ctype.h>
#include <errno.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define WIDTH 375
#define HEIGHT 320
static uint32_t pixels[WIDTH*HEIGHT];
static volatile sig_atomic_t stopped;
struct display;
#ifndef USB_STATUS_HEADLESS
extern struct display *q2_display_open(void);
extern void q2_display_close(struct display *);
extern int q2_display_present(struct display *,const uint32_t *,unsigned);
#else
static struct display *q2_display_open(void) { return (struct display *)1; }
static void q2_display_close(struct display *d) { (void)d; }
static int q2_display_present(struct display *d,const uint32_t *p,unsigned n) { (void)d;(void)p;return n==WIDTH*HEIGHT?0:-1; }
#endif
/* Original small bitmap lettering, rows from top to bottom, five columns. */
static const unsigned char font[36][7]={
 {14,17,17,31,17,17,17},{30,17,17,30,17,17,30},{14,17,16,16,16,17,14},
 {30,17,17,17,17,17,30},{31,16,16,30,16,16,31},{31,16,16,30,16,16,16},
 {14,17,16,23,17,17,15},{17,17,17,31,17,17,17},{31,4,4,4,4,4,31},
 {7,2,2,2,18,18,12},{17,18,20,24,20,18,17},{16,16,16,16,16,16,31},
 {17,27,21,21,17,17,17},{17,25,21,19,17,17,17},{14,17,17,17,17,17,14},
 {30,17,17,30,16,16,16},{14,17,17,17,21,18,13},{30,17,17,30,20,18,17},
 {15,16,16,14,1,1,30},{31,4,4,4,4,4,4},{17,17,17,17,17,17,14},
 {17,17,17,17,17,10,4},{17,17,17,21,21,21,10},{17,17,10,4,10,17,17},
 {17,17,10,4,4,4,4},{31,1,2,4,8,16,31},
 {14,17,19,21,25,17,14},{4,12,4,4,4,4,14},{14,17,1,2,4,8,31},
 {30,1,1,14,1,1,30},{2,6,10,18,31,2,2},{31,16,16,30,1,1,30},
 {14,16,16,30,17,17,14},{31,1,2,4,8,8,8},{14,17,17,14,17,17,14},
 {14,17,17,15,1,1,14}
};
static void box(int x,int y,int w,int h,uint32_t color) {
 for(int j=y;j<y+h;j++) for(int i=x;i<x+w;i++)
  if(i>=0 && i<WIDTH && j>=0 && j<HEIGHT) pixels[j*WIDTH+i]=color;
}
static void text(const char *s,int y,int scale,uint32_t color) {
 int x=(WIDTH-((int)strlen(s)*6-1)*scale)/2;
 for(;*s;s++,x+=6*scale) {
  int c=toupper((unsigned char)*s),index=c>='A'&&c<='Z'?c-'A':c>='0'&&c<='9'?c-'0'+26:-1;
  if(index<0) continue;
  for(int row=0;row<7;row++) for(int col=0;col<5;col++)
   if(font[index][row]&(1U<<(4-col))) box(x+col*scale,y+row*scale,scale,scale,color);
 }
}
static void line(int x0,int y0,int x1,int y1,uint32_t color) {
 int dx=abs(x1-x0),sx=x0<x1?1:-1,dy=-abs(y1-y0),sy=y0<y1?1:-1,error=dx+dy;
 for(;;) { box(x0-2,y0-2,5,5,color);if(x0==x1&&y0==y1)break;int e=2*error;if(e>=dy){error+=dy;x0+=sx;}if(e<=dx){error+=dx;y0+=sy;} }
}
static void circle(int x,int y,int r,uint32_t color) {
 for(int j=-r;j<=r;j++)for(int i=-r;i<=r;i++)if(i*i+j*j<=r*r)box(x+i,y+j,1,1,color);
}
static void render(const char *phase,const char *error) {
 const uint32_t ink=0x22354b,accent=0x226aac,muted=0x576778;
 for(unsigned i=0;i<WIDTH*HEIGHT;i++)pixels[i]=0xf3f5f7;
 text("USB Drive Mode",35,3,ink);box(65,67,245,2,0xcbd4df);
 line(187,137,187,83,accent);line(187,111,161,96,accent);line(161,96,161,86,accent);
 line(187,121,214,105,accent);line(214,105,214,88,accent);
 circle(187,140,7,accent);circle(161,81,6,accent);box(208,78,13,13,accent);
 for(int row=0;row<10;row++)box(187-row,74+row,2*row+1,1,accent);
 if(error && *error) {
  text("USB needs attention",173,2,ink);
  text(!strcmp(error,"eject-required")?"Eject or unplug to return":"Check your computer",199,2,muted);
  text("Card access is protected",243,1,muted);
  text("The player is paused",270,1,muted);
 } else if(!strcmp(phase,"local-ready")) {
  text("Finishing setup",173,2,ink);text("Please wait",202,2,muted);
  text("Card is local again",246,1,muted);
 } else if(!strcmp(phase,"returning")) {
  text("Returning to player",173,2,ink);text("Please wait",202,2,muted);
 } else if(!strcmp(phase,"preparing") || !strcmp(phase,"detached") || !strcmp(phase,"armed")) {
  text("Connecting to computer",173,2,ink);text("Please wait",202,2,muted);
 } else {
  text("Edit files on your computer",173,2,ink);
  text("Eject or unplug to return",241,2,ink);
  text("The player is paused",272,1,muted);
 }
}
static int snapshot(const char *path) {
 FILE *f=fopen(path,"wb");if(!f)return -1;
 if(fprintf(f,"P6\n%d %d\n255\n",WIDTH,HEIGHT)<0){fclose(f);return -1;}
 for(unsigned i=0;i<WIDTH*HEIGHT;i++) { unsigned char rgb[]={pixels[i]>>16,pixels[i]>>8,pixels[i]};if(fwrite(rgb,1,3,f)!=3){fclose(f);return -1;} }
 return fclose(f);
}
static void metadata(const char *path,char *out,size_t size) {
 out[0]=0;FILE *f=fopen(path,"r");if(!f)return;
 if(fgets(out,(int)size,f))out[strcspn(out,"\r\n")]=0;
 fclose(f);
}
static void stop_signal(int signal) { (void)signal;stopped=1; }
static int backlight(int on) {
#ifndef USB_STATUS_HEADLESS
 unsigned maximum=0;FILE *f=fopen("/sys/class/backlight/backlight/max_brightness","r");
 if(!f)return -1;
 int valid=fscanf(f,"%u",&maximum)==1 && maximum>0 && maximum<=65535;fclose(f);if(!valid)return -1;
 f=fopen("/sys/class/backlight/backlight/brightness","w");if(!f)return -1;
 int result=fprintf(f,"%u\n",on?(maximum*55+99)/100:0)<0?-1:0;
 if(fclose(f))result=-1;
 return result;
#else
 (void)on;return 0;
#endif
}
int main(int argc,char **argv) {
 const char *phase="exported",*error="",*output=NULL,*run="/run/q2";
 for(int i=1;i<argc;i++) {
  if(!strcmp(argv[i],"--snapshot") && i+1<argc)output=argv[++i];
  else if(!strcmp(argv[i],"--phase") && i+1<argc)phase=argv[++i];
  else if(!strcmp(argv[i],"--error") && i+1<argc)error=argv[++i];
#ifdef USB_STATUS_HEADLESS
  else if(!strcmp(argv[i],"--fixture") && i+1<argc)run=argv[++i];
#endif
  else return 2;
 }
 if(output){render(phase,error);return snapshot(output)?1:0;}
 if(chdir("/"))return 1;
 char phase_path[512],error_path[512],ready[512],last[512],held[512];
 if(snprintf(phase_path,sizeof phase_path,"%s/usb-storage/phase",run)>=(int)sizeof phase_path ||
    snprintf(error_path,sizeof error_path,"%s/usb-storage/error",run)>=(int)sizeof error_path ||
    snprintf(ready,sizeof ready,"%s/usb-status.ready",run)>=(int)sizeof ready ||
    snprintf(last,sizeof last,"%s/usb-status.ppm",run)>=(int)sizeof last ||
    snprintf(held,sizeof held,"%s/../q2nd/start-held",run)>=(int)sizeof held)return 2;
 signal(SIGTERM,stop_signal);signal(SIGINT,stop_signal);
 struct display *display=q2_display_open();if(!display){perror("USB status display");return 1;}
 char old_phase[64]="",old_error[64]="",new_phase[64],new_error[64];int first=1,result=0;
 while(!stopped) {
  metadata(phase_path,new_phase,sizeof new_phase);metadata(error_path,new_error,sizeof new_error);
  if(!*new_phase)strcpy(new_phase,access(held,F_OK)==0?"local-ready":"returning");
  if(first || strcmp(old_phase,new_phase) || strcmp(old_error,new_error)) {
   render(new_phase,new_error);
   if(q2_display_present(display,pixels,WIDTH*HEIGHT) || backlight(1)){perror("USB status present");result=1;break;}
#ifdef USB_STATUS_HEADLESS
   if(snapshot(last)){result=1;break;}
#endif
   strcpy(old_phase,new_phase);strcpy(old_error,new_error);
   if(first) {FILE *f=fopen(ready,"w");if(!f){result=1;break;}fprintf(f,"%ld\n",(long)getpid());if(fclose(f)){result=1;break;}first=0;}
  }
  struct timespec delay={.tv_sec=0,.tv_nsec=250000000};nanosleep(&delay,NULL);
 }
 unlink(ready);backlight(0);q2_display_close(display);return result;
}
