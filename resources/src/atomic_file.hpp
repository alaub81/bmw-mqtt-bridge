#pragma once

#include <cerrno>
#include <cstring>
#include <filesystem>
#include <string>
#include <vector>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>
#include "bridge_log.hpp"

static bool write_file_atomic(const std::string& final_path,
                              const std::string& data)
{
    namespace fs = std::filesystem;

    fs::path fpath(final_path);
    fs::path dir = fpath.parent_path();
    std::error_code ec;
    fs::create_directories(dir, ec); // File creation below reports failures.

    // Create the temporary file in the target directory.
    std::string tmpl = (dir / (fpath.filename().string() + ".tmp.XXXXXX")).string();
    std::vector<char> buf(tmpl.begin(), tmpl.end());
    buf.push_back('\0');

    int tfd = ::mkstemp(buf.data()); // Creates a unique temporary file.
    if (tfd < 0) {
        BMB_LOG(error) << "[bridge] mkstemp failed: " << std::strerror(errno) << "\n";
        return false;
    }

    // Set permissions independently of umask.
    if (::fchmod(tfd, 0600) != 0) {
        BMB_LOG(error) << "[bridge] fchmod failed: " << std::strerror(errno) << "\n";
        ::close(tfd);
        ::unlink(buf.data());
        return false;
    }

    // Write the complete payload.
    const char* p = data.data();
    ssize_t left = (ssize_t)data.size();
    while (left > 0) {
        ssize_t n = ::write(tfd, p, left);
        if (n <= 0) {
            if (n == 0) errno = EIO;
            if (errno == EINTR) continue;
            BMB_LOG(error) << "[bridge] write failed: " << std::strerror(errno) << "\n";
            ::close(tfd);
            ::unlink(buf.data());
            return false;
        }
        left -= n; p += n;
    }

    // Flush file contents.
    if (::fsync(tfd) != 0) {
        BMB_LOG(error) << "[bridge] fsync(tmp) failed: " << std::strerror(errno) << "\n";
        ::close(tfd);
        ::unlink(buf.data());
        return false;
    }
    ::close(tfd);

    // Replace atomically within the same filesystem.
    if (::rename(buf.data(), final_path.c_str()) != 0) {
        BMB_LOG(error) << "[bridge] rename failed: " << std::strerror(errno)
                  << " (errno=" << errno << ")\n";
        ::unlink(buf.data());
        return false;
    }

    // Flush the directory to persist the rename.
    int dfd = ::open(dir.c_str(), O_RDONLY | O_DIRECTORY);
    if (dfd < 0) {
        BMB_LOG(error) << "[bridge] committed file but cannot open directory for sync\n";
        return false;
    }
    const bool durable = ::fsync(dfd) == 0;
    ::close(dfd);
    if (!durable) BMB_LOG(error) << "[bridge] file committed but directory sync failed\n";
    return durable;
}

