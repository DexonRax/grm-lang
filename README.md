# GRM

A minimal language that fixes C's most glaring omission — struct methods — while transpiling to clean, readable C that any C programmer can follow.

GRM is not a C++ alternative. It is a thin syntactic layer over C. The transpiler output is intentionally boring.

---

## The idea

Every C programmer already writes this:

```c
void Human_birthday(Human* self) {
    self->age++;
}

Human_birthday(&john);
```

GRM just lets you write it like this instead:

```c
fn void birthday(Human& self) {
    self.age = self.age + 1;
}

john.birthday();
```

The compiler sees the same thing either way. GRM is a name mangler and syntax sugar layer, nothing more.

---

## Requirements

- Python 3.10+
- GCC (or any C11-compatible compiler)

No dependencies outside the standard library.

---

## Files

| File | Purpose |
|------|---------|
| `grmc.py` | Transpiler — GRM → C |
| `grm_interpreter.py` | Tree-walking interpreter for running GRM directly |

---

## Quick start

**`human.grm`**
```c
struct Human {
    str name;
    int age;
    float height;

    fn void print(const Human& self) {
        printf(f"{self.name} | age: {self.age} | height: {self.height}cm\n");
    }

    fn bool isAdult(const Human& self) {
        return self.age >= 18;
    }

    fn void birthday(Human& self) {
        self.age = self.age + 1;
        printf(f"Happy birthday {self.name}! Now {self.age}.\n");
    }
}
```

**`main.grm`**
```c
import "human";

fn int main() {
    Human alice = { .name = "Alice", .age = 17, .height = 171.0f };

    alice.print();
    alice.birthday();

    if (alice.isAdult()) {
        printf(f"{alice.name} is now an adult.\n");
    }

    return 0;
}
```

**Transpile and compile:**
```bash
python grmc.py human.grm --split -d out/
python grmc.py main.grm  --split -d out/
gcc out/human.c out/main.c -o prog
./prog
```

**Or run directly without transpiling:**
```bash
python grm_interpreter.py main.grm
```

---

## Language reference

### Types

| GRM | C |
|-----|---|
| `int` | `int` |
| `float` | `float` |
| `bool` | `bool` |
| `str` | `const char*` |
| `void` | `void` |
| `T&` | `T*` |
| `const T&` | `const T*` |

Struct names pass through as-is.

---

### Structs

Fields and methods live in the same block.

```c
struct Point {
    float x;
    float y;

    fn float length(const Point& self) {
        return self.x * self.x + self.y * self.y;
    }
}
```

**Transpiles to:**
```c
typedef struct Point Point;

struct Point {
    float x;
    float y;
};

float Point_length(const Point* self);

float Point_length(const Point* self) {
    return ((self->x * self->x) + (self->y * self->y));
}
```

Method calls are rewritten automatically:

```c
p.length()        →  Point_length(&p)
self.x            →  self->x
```

---

### Struct literals

Uses C designated initializer syntax:

```c
Point p = { .x = 3.0f, .y = 4.0f };
```

---

### Functions

```c
fn int add(int a, int b) {
    return a + b;
}
```

---

### f-strings

Interpolate expressions directly into strings:

```c
printf(f"Hello, {self.name}! You are {self.age} years old.\n");
```

Transpiles to a `printf` call with inferred format specifiers (`%d`, `%g`, `%s`) based on expression type.

When an f-string is passed as an argument to a non-printf function (e.g. Raylib's `DrawText`), it is routed through `GrmFmt` instead:

```c
DrawText(f"Score: {self.score}", 10, 40, 20, BLACK);
// becomes:
DrawText(GrmFmt("Score: %d", self->score), 10, 40, 20, BLACK);
```

> **Note:** `GrmFmt` uses a single static buffer. Do not use it twice in the same function call expression.

---

### `const` methods

Mark methods that do not mutate the struct:

```c
fn str getName(const Human& self) {
    return self.name;
}
```

Transpiles to `const Human* self`, enforced by the C compiler.

---

### `defer`

Runs a statement at the end of the current function scope, before every `return`:

```c
fn int main() {
    defer CloseWindow();

    // ... program logic ...

    return 0;  // CloseWindow() emitted here automatically
}
```

Defers are emitted in reverse order, like a stack.

---

### `import`

Imports another GRM module. Transpiles to `#include "module.h"`:

```c
import "human";
```

Transpile dependency modules first so the transpiler can resolve types across files.

---

### `extern`

Includes a C header without transpiling it. Used for third-party C libraries:

```c
extern "raylib.h";    // → #include "raylib.h"
extern <raylib.h>;    // → #include <raylib.h>
```

All functions, constants, and types from the external header are available as-is. The transpiler passes unknown identifiers through verbatim.

---

## Transpiler usage

### Single-file output

Produces one self-contained `.c` file. Good for small single-file programs.

```bash
python grmc.py input.grm              # writes input.c
python grmc.py input.grm -o output.c  # explicit output path
python grmc.py input.grm --stdout     # print to stdout
```

### Split output (recommended for multi-file projects)

Produces a `.h` / `.c` pair per module.

```bash
python grmc.py module.grm --split -d out/
```

Output:
```
out/
  module.h    ← typedefs, struct bodies, forward declarations
  module.c    ← method and function bodies
```

If any module uses f-strings as foreign function arguments, `grm_runtime.h` and `grm_runtime.c` are also generated automatically.

---

## Multi-file projects

Transpile in dependency order — modules with no imports first:

```bash
python grmc.py vec.grm    --split -d out/
python grmc.py entity.grm --split -d out/
python grmc.py main.grm   --split -d out/
```

Then compile all `.c` files together. If `grm_runtime.c` was generated, include it first:

```bash
gcc out/grm_runtime.c out/vec.c out/entity.c out/main.c -o prog
```

---

## Using with Raylib

```c
extern "raylib.h";

struct Game {
    int score;

    fn void draw(const Game& self) {
        BeginDrawing();
        ClearBackground(RAYWHITE);
        DrawText(f"Score: {self.score}", 10, 10, 20, BLACK);
        EndDrawing();
    }
}

fn int main() {
    Game game = { .score = 0 };

    InitWindow(800, 600, "GRM");
    SetTargetFPS(60);

    defer CloseWindow();

    while (!WindowShouldClose()) {
        game.score = game.score + 1;
        game.draw();
    }

    return 0;
}
```

```bash
python grmc.py game.grm --split -d out/
gcc out/grm_runtime.c out/game.c -lraylib -lm -ldl -lpthread -o game
```

---

## What GRM is not

- Not a memory-safe language. You still manage memory manually.
- Not an object-oriented language. No inheritance, no vtables, no virtual dispatch.
- Not a replacement for C++. If you need templates, exceptions, or RAII, use C++.
- Not trying to be Rust or Zig. No borrow checker, no comptime, no error unions.

GRM is C with method call syntax and f-strings. That is intentional.
