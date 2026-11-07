#pragma once
#include <iomanip>
#include <sstream>
#include <string>
#include <fstream>
#include <openssl/evp.h>

// SHA1 is used only for Mojang's content-addressed asset metadata compatibility.
inline std::string sha1_file(const std::string& path) {
    std::ifstream f(path, std::ios::binary);
    if (!f) return "";
    EVP_MD_CTX* ctx = EVP_MD_CTX_new();
    if (!ctx) return "";
    if (EVP_DigestInit_ex(ctx, EVP_sha1(), nullptr) != 1) {
        EVP_MD_CTX_free(ctx);
        return "";
    }
    char buffer[65536];
    while (f.read(buffer, sizeof(buffer)) || f.gcount() > 0) {
        EVP_DigestUpdate(ctx, buffer, static_cast<size_t>(f.gcount()));
    }
    unsigned char digest[EVP_MAX_MD_SIZE];
    unsigned int length = 0;
    if (EVP_DigestFinal_ex(ctx, digest, &length) != 1) {
        EVP_MD_CTX_free(ctx);
        return "";
    }
    EVP_MD_CTX_free(ctx);
    std::ostringstream out;
    for (unsigned int i = 0; i < length; ++i) {
        out << std::hex << std::setw(2) << std::setfill('0') << static_cast<int>(digest[i]);
    }
    return out.str();
}
