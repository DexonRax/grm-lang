# Variables
PYTHON = python3
CC = gcc
CFLAGS = -Wall -Wextra -O2
LIBS = -lraylib -lm

# Find all .grm files in the current directory
SOURCES = $(wildcard *.grm)

# Generate the names of the final executables (strip .grm extension)
TARGETS = $(basename $(SOURCES))

# Default rule: build all executables
all: $(TARGETS)

# Rule to build each final executable from its corresponding .grm file
%: %.grm
	@echo "Transpiling $< to $*.c..."
	$(PYTHON) ../grmc.py $<
	@echo "Compiling $*.c to $*..."
	$(CC) $(CFLAGS) $*.c -o $* $(LIBS)

# Clean up generated C files and executables
clean:
	@echo "Cleaning up generated files..."
	rm -f $(TARGETS) $(addsuffix .c, $(TARGETS))

.PHONY: all clean
