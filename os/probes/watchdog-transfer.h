/* One-shot watchdog FD transfer. Only root in this disposable image may claim. */
#ifndef Q2_WATCHDOG_TRANSFER_H
#define Q2_WATCHDOG_TRANSFER_H
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>
static int q2_watchdog_claim(const char *path)
{
    int socket_fd = socket(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0), fd = -1;
    if (socket_fd < 0) return -1;
    struct sockaddr_un address = {.sun_family = AF_UNIX};
    if (strlen(path) >= sizeof(address.sun_path)) { errno = ENAMETOOLONG; goto fail; }
    strcpy(address.sun_path, path);
    if (connect(socket_fd, (void *)&address, sizeof(address)) < 0) goto fail;
    struct pollfd p = {.fd = socket_fd, .events = POLLIN};
    if (poll(&p, 1, 1000) != 1) { errno = ETIMEDOUT; goto fail; }
    char byte = 0, control[CMSG_SPACE(sizeof(int))] = {0};
    struct iovec iov = {.iov_base = &byte, .iov_len = 1};
    struct msghdr message = {.msg_iov = &iov, .msg_iovlen = 1,
        .msg_control = control, .msg_controllen = sizeof(control)};
    ssize_t size = recvmsg(socket_fd, &message, MSG_CMSG_CLOEXEC);
    struct cmsghdr *c = CMSG_FIRSTHDR(&message);
    if (size == 1 && !(message.msg_flags & (MSG_CTRUNC | MSG_TRUNC)) && c &&
        c->cmsg_level == SOL_SOCKET && c->cmsg_type == SCM_RIGHTS && c->cmsg_len == CMSG_LEN(sizeof(int)))
        memcpy(&fd, CMSG_DATA(c), sizeof(fd));
    if (fd < 0 || byte != 'W') { errno = EPROTO; goto fail; }
    /* The server stopped feeding before sending. ACK never resumes feeding. */
    if (send(socket_fd, "A", 1, MSG_NOSIGNAL) != 1) goto fail;
    close(socket_fd);
    return fd;
fail: {
    int saved = errno;
    if (fd >= 0) close(fd);
    close(socket_fd); errno = saved;
    return -1;
}}
#endif
