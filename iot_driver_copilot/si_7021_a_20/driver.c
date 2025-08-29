// Si7021-A20 HTTP Proxy Driver
// Environment Variables:
//   SI7021_I2C_DEV - I2C bus device path (e.g., /dev/i2c-1)
//   SI7021_I2C_ADDR - I2C address in hex (default 0x40)
//   SHIFU_HTTP_HOST - HTTP server bind address (default 0.0.0.0)
//   SHIFU_HTTP_PORT - HTTP server port (default 8080)

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <unistd.h>
#include <stdint.h>
#include <fcntl.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <netinet/in.h>
#include <poll.h>
#include <time.h>
#include <errno.h>
#include <linux/i2c-dev.h>

// ---- Si7021 Registers/Commands ----
#define SI7021_ADDR_DEFAULT   0x40

#define CMD_MEASURE_RH_NOHOLD     0xF5
#define CMD_MEASURE_TEMP_NOHOLD   0xF3
#define CMD_READ_TEMP_FROM_PREV_RH 0xE0
#define CMD_RESET                 0xFE
#define CMD_READ_USER_REG         0xE7
#define CMD_READ_FW_REV           0x84
#define CMD_READ_FW_REV_ARG       0xB8
#define CMD_READ_ID1_1            0xFA
#define CMD_READ_ID1_2            0x0F
#define CMD_READ_ID2_1            0xFC
#define CMD_READ_ID2_2            0xC9

#define MAX_HTTP_REQ 2048
#define MAX_HTTP_RESP 2048

// --- Helper for timestamp ---
static void http_time_rfc3339(char* buf, size_t sz) {
    time_t t = time(NULL);
    struct tm tm;
    gmtime_r(&t, &tm);
    strftime(buf, sz, "%Y-%m-%dT%H:%M:%SZ", &tm);
}

// ---- I2C helpers ----
typedef struct {
    int fd;
    uint8_t addr;
} si7021_i2c_t;

// Open I2C bus and set slave address
static int si7021_i2c_open(si7021_i2c_t* dev, const char* path, uint8_t addr) {
    dev->fd = open(path, O_RDWR);
    if (dev->fd < 0) return -1;
    if (ioctl(dev->fd, I2C_SLAVE, addr) < 0) {
        close(dev->fd);
        return -1;
    }
    dev->addr = addr;
    return 0;
}

static void si7021_i2c_close(si7021_i2c_t* dev) {
    if (dev->fd >= 0) close(dev->fd);
    dev->fd = -1;
}

// --- Si7021 Commands ---
static int si7021_reset(si7021_i2c_t* dev) {
    uint8_t cmd = CMD_RESET;
    if (write(dev->fd, &cmd, 1) != 1) return -1;
    usleep(50000); // 50 ms
    return 0;
}

// Read N bytes after issuing command (no-argument)
static int si7021_read_cmd(si7021_i2c_t* dev, uint8_t cmd, uint8_t* buf, int n, int delay_us) {
    if (write(dev->fd, &cmd, 1) != 1) return -1;
    usleep(delay_us);
    int r = read(dev->fd, buf, n);
    return (r == n) ? 0 : -1;
}

// Measure relative humidity (no hold)
static int si7021_measure_humidity(si7021_i2c_t* dev, float* rh) {
    uint8_t buf[3];
    if (si7021_read_cmd(dev, CMD_MEASURE_RH_NOHOLD, buf, 3, 25000) < 0) return -1;
    uint16_t raw = (buf[0] << 8) | buf[1];
    *rh = ((125.0 * raw) / 65536.0) - 6.0;
    return 0;
}

// Measure temperature (no hold)
static int si7021_measure_temperature(si7021_i2c_t* dev, float* temp) {
    uint8_t buf[3];
    if (si7021_read_cmd(dev, CMD_MEASURE_TEMP_NOHOLD, buf, 3, 25000) < 0) return -1;
    uint16_t raw = (buf[0] << 8) | buf[1];
    *temp = ((175.72 * raw) / 65536.0) - 46.85;
    return 0;
}

// Read temperature from last RH measurement
static int si7021_read_temp_from_last_rh(si7021_i2c_t* dev, float* temp) {
    uint8_t cmd = CMD_READ_TEMP_FROM_PREV_RH;
    uint8_t buf[3];
    if (write(dev->fd, &cmd, 1) != 1) return -1;
    usleep(5000);
    if (read(dev->fd, buf, 2) != 2) return -1;
    uint16_t raw = (buf[0] << 8) | buf[1];
    *temp = ((175.72 * raw) / 65536.0) - 46.85;
    return 0;
}

// Read firmware revision
static int si7021_read_fw_rev(si7021_i2c_t* dev, char* buf, size_t bufsz) {
    uint8_t cmd[2] = {CMD_READ_FW_REV, CMD_READ_FW_REV_ARG};
    if (write(dev->fd, cmd, 2) != 2) return -1;
    usleep(5000);
    uint8_t rev;
    if (read(dev->fd, &rev, 1) != 1) return -1;
    if (rev == 0xFF)
        snprintf(buf, bufsz, "1.0");
    else if (rev == 0x20)
        snprintf(buf, bufsz, "2.0");
    else
        snprintf(buf, bufsz, "unk(0x%02X)", rev);
    return 0;
}

// Read serial number (combine 8 bytes from two reads)
static int si7021_read_serial(si7021_i2c_t* dev, uint64_t* sn) {
    uint8_t cmd1[2] = {CMD_READ_ID1_1, CMD_READ_ID1_2};
    uint8_t cmd2[2] = {CMD_READ_ID2_1, CMD_READ_ID2_2};
    uint8_t buf1[8], buf2[6];
    // First part
    if (write(dev->fd, cmd1, 2) != 2) return -1;
    usleep(5000);
    if (read(dev->fd, buf1, 8) != 8) return -1;
    // Second part
    if (write(dev->fd, cmd2, 2) != 2) return -1;
    usleep(5000);
    if (read(dev->fd, buf2, 6) != 6) return -1;
    // Serial number: combine
    // SNB_3, SNB_2, SNB_1, SNB_0 from buf2[0,1,3,4]
    // SNA_3, SNA_2, SNA_1, SNA_0 from buf1[0,2,4,6]
    uint32_t sna = (buf1[0]<<24) | (buf1[2]<<16) | (buf1[4]<<8) | buf1[6];
    uint32_t snb = (buf2[0]<<24) | (buf2[1]<<16) | (buf2[3]<<8) | buf2[4];
    *sn = ((uint64_t)sna << 32) | snb;
    return 0;
}

// ---- HTTP Server ----

typedef struct {
    int listen_fd;
    char host[64];
    int port;
} http_server_t;

static int http_server_init(http_server_t* srv, const char* host, int port) {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) return -1;
    int yes = 1;
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &yes, sizeof(yes));
    struct sockaddr_in addr;
    addr.sin_family = AF_INET;
    addr.sin_port = htons(port);
    if (!host || strcmp(host, "0.0.0.0") == 0)
        addr.sin_addr.s_addr = INADDR_ANY;
    else
        addr.sin_addr.s_addr = inet_addr(host);
    if (bind(fd, (struct sockaddr*)&addr, sizeof(addr)) < 0) {
        close(fd);
        return -1;
    }
    if (listen(fd, 8) < 0) {
        close(fd);
        return -1;
    }
    srv->listen_fd = fd;
    strncpy(srv->host, host, sizeof(srv->host));
    srv->port = port;
    return 0;
}

// Helper: Send HTTP response (headers + body)
static void http_send_json(int fd, int code, const char* body) {
    char buf[MAX_HTTP_RESP];
    snprintf(buf, sizeof(buf),
        "HTTP/1.1 %d OK\r\n"
        "Content-Type: application/json\r\n"
        "Content-Length: %zu\r\n"
        "Connection: close\r\n"
        "\r\n"
        "%s", code, strlen(body), body);
    send(fd, buf, strlen(buf), 0);
}

static void http_send_plain(int fd, int code, const char* msg) {
    char buf[MAX_HTTP_RESP];
    snprintf(buf, sizeof(buf),
        "HTTP/1.1 %d OK\r\n"
        "Content-Type: text/plain\r\n"
        "Content-Length: %zu\r\n"
        "Connection: close\r\n"
        "\r\n"
        "%s", code, strlen(msg), msg);
    send(fd, buf, strlen(buf), 0);
}

static void http_send_404(int fd) {
    const char* msg = "{\"error\": \"Not found\"}";
    char buf[256];
    snprintf(buf, sizeof(buf),
        "HTTP/1.1 404 Not Found\r\n"
        "Content-Type: application/json\r\n"
        "Content-Length: %zu\r\n"
        "Connection: close\r\n"
        "\r\n"
        "%s", strlen(msg), msg);
    send(fd, buf, strlen(buf), 0);
}

static void http_send_405(int fd) {
    const char* msg = "{\"error\": \"Method not allowed\"}";
    char buf[256];
    snprintf(buf, sizeof(buf),
        "HTTP/1.1 405 Method Not Allowed\r\n"
        "Content-Type: application/json\r\n"
        "Content-Length: %zu\r\n"
        "Connection: close\r\n"
        "\r\n"
        "%s", strlen(msg), msg);
    send(fd, buf, strlen(buf), 0);
}

// Parse HTTP request line and path
static int http_parse_request(int fd, char* method, char* path, char* body, size_t bodysz) {
    char buf[MAX_HTTP_REQ+1] = {0};
    int n = recv(fd, buf, MAX_HTTP_REQ, 0);
    if (n <= 0) return -1;
    buf[n] = 0;
    char* reqline = strtok(buf, "\r\n");
    if (!reqline) return -1;
    if (sscanf(reqline, "%7s %127s", method, path) != 2) return -1;
    // Find body for POST
    char* cl = strstr(buf, "Content-Length:");
    int clen = 0;
    if (cl) sscanf(cl, "Content-Length: %d", &clen);
    char* hdr_end = strstr(buf, "\r\n\r\n");
    if (hdr_end && clen > 0) {
        hdr_end += 4;
        int avail = n - (hdr_end - buf);
        int copy = (avail > clen) ? clen : avail;
        if (copy > bodysz-1) copy = bodysz-1;
        memcpy(body, hdr_end, copy);
        body[copy] = 0;
    } else if (body) {
        body[0] = 0;
    }
    return 0;
}

// --- HTTP Handlers ---
static void handle_get_temp(int fd, si7021_i2c_t* dev) {
    float temp;
    char timestamp[32];
    char body[256];
    if (si7021_measure_temperature(dev, &temp) < 0) {
        http_send_plain(fd, 500, "Failed to read temperature");
        return;
    }
    http_time_rfc3339(timestamp, sizeof(timestamp));
    snprintf(body, sizeof(body),
        "{\"temperature_c\": %.2f, \"timestamp\": \"%s\"}",
        temp, timestamp
    );
    http_send_json(fd, 200, body);
}

static void handle_get_humidity(int fd, si7021_i2c_t* dev) {
    float rh;
    char timestamp[32];
    char body[256];
    if (si7021_measure_humidity(dev, &rh) < 0) {
        http_send_plain(fd, 500, "Failed to read humidity");
        return;
    }
    http_time_rfc3339(timestamp, sizeof(timestamp));
    snprintf(body, sizeof(body),
        "{\"humidity_rh\": %.2f, \"timestamp\": \"%s\"}",
        rh, timestamp
    );
    http_send_json(fd, 200, body);
}

static void handle_device_info(int fd, si7021_i2c_t* dev) {
    char fw[16] = "unknown";
    uint64_t sn = 0;
    si7021_read_fw_rev(dev, fw, sizeof(fw));
    si7021_read_serial(dev, &sn);
    char body[512];
    snprintf(body, sizeof(body),
        "{\"device_model\": \"Si7021-A20\","
        "\"manufacturer\": \"Silicon Laboratories\","
        "\"firmware_revision\": \"%s\","
        "\"serial_number\": \"%016llX\"}",
        fw, (unsigned long long)sn
    );
    http_send_json(fd, 200, body);
}

static void handle_reset(int fd, si7021_i2c_t* dev) {
    if (si7021_reset(dev) < 0) {
        http_send_plain(fd, 500, "Failed to reset device");
        return;
    }
    http_send_json(fd, 200, "{\"status\": \"reset issued\"}");
}

// ---- Main HTTP dispatch ----
static void http_dispatch(int fd, si7021_i2c_t* dev) {
    char method[8], path[128], body[512];
    if (http_parse_request(fd, method, path, body, sizeof(body)) < 0) {
        http_send_plain(fd, 400, "Bad request");
        return;
    }
    if (strcmp(method, "GET") == 0) {
        if (strcmp(path, "/sensors/temp") == 0) {
            handle_get_temp(fd, dev);
        } else if (strcmp(path, "/sensors/humidity") == 0) {
            handle_get_humidity(fd, dev);
        } else if (strcmp(path, "/device/info") == 0) {
            handle_device_info(fd, dev);
        } else {
            http_send_404(fd);
        }
    } else if (strcmp(method, "POST") == 0) {
        if (strcmp(path, "/commands/reset") == 0) {
            handle_reset(fd, dev);
        } else {
            http_send_404(fd);
        }
    } else {
        http_send_405(fd);
    }
}

// ---- Main Loop ----
int main() {
    const char* i2c_env = getenv("SI7021_I2C_DEV");
    const char* addr_env = getenv("SI7021_I2C_ADDR");
    const char* host_env = getenv("SHIFU_HTTP_HOST");
    const char* port_env = getenv("SHIFU_HTTP_PORT");

    const char* i2c_dev = i2c_env ? i2c_env : "/dev/i2c-1";
    uint8_t i2c_addr = addr_env ? (uint8_t)strtol(addr_env, NULL, 0) : SI7021_ADDR_DEFAULT;
    const char* listen_host = host_env ? host_env : "0.0.0.0";
    int listen_port = port_env ? atoi(port_env) : 8080;

    si7021_i2c_t dev;
    if (si7021_i2c_open(&dev, i2c_dev, i2c_addr) < 0) {
        fprintf(stderr, "Failed to open I2C device %s at address 0x%02X\n", i2c_dev, i2c_addr);
        exit(1);
    }

    http_server_t srv;
    if (http_server_init(&srv, listen_host, listen_port) < 0) {
        fprintf(stderr, "HTTP server bind failed at %s:%d\n", listen_host, listen_port);
        si7021_i2c_close(&dev);
        exit(1);
    }

    printf("Si7021 HTTP driver started on %s:%d (I2C %s@0x%02X)\n", listen_host, listen_port, i2c_dev, i2c_addr);

    while (1) {
        struct sockaddr_in cliaddr;
        socklen_t clilen = sizeof(cliaddr);
        int cfd = accept(srv.listen_fd, (struct sockaddr*)&cliaddr, &clilen);
        if (cfd < 0) continue;
        http_dispatch(cfd, &dev);
        close(cfd);
    }

    si7021_i2c_close(&dev);
    close(srv.listen_fd);
    return 0;
}