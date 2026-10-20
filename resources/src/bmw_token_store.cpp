// Authentication helper: secrets arrive on stdin, never in process arguments.
#include <iostream>
#include "atomic_file.hpp"
#include "token_store.hpp"

int main(int argc, char** argv) {
    if (argc != 2) return 2;
    std::string response;
    char buffer[4096];
    while (std::cin.read(buffer, sizeof(buffer)) || std::cin.gcount()) {
        response.append(buffer, static_cast<size_t>(std::cin.gcount()));
        if (response.size() > 1024 * 1024) return 1;
    }
    token_validation::Tokens tokens;
    if (!std::cin.eof() || !token_store::decode(response, tokens, false) ||
        !token_store::save(argv[1], tokens, write_file_atomic)) {
        std::cerr << "Cannot validate or commit token bundle (response omitted)\n";
        return 1;
    }
    return 0;
}
