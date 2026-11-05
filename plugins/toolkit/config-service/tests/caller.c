/* Synthetic signed caller for native tests ONLY. Never shipped or enrolled. */
#include <sys/socket.h>
#include <sys/wait.h>
#include <unistd.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifndef BRIDGE
#error Test bridge path required
#endif
int main(void) {
    int pair[2]; if(socketpair(AF_UNIX,SOCK_STREAM,0,pair))return 1;
    pid_t child=fork();if(child<0)return 2;
    if(!child){close(pair[0]);if(dup2(pair[1],0)<0)_exit(3);close(pair[1]);execl(BRIDGE,BRIDGE,NULL);_exit(4);}
    close(pair[1]);char buf[4096];ssize_t n;
    while((n=read(0,buf,sizeof(buf)))>0){ssize_t offset=0;while(offset<n){ssize_t r=write(pair[0],buf+offset,n-offset);if(r<=0)return 5;offset+=r;}if(memchr(buf,'\n',(size_t)n))break;}
    shutdown(pair[0],SHUT_WR);int status;waitpid(child,&status,0);close(pair[0]);
    return WIFEXITED(status)?WEXITSTATUS(status):6;
}
