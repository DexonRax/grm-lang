"""
grmc.py — GRM to C transpiler
Usage:
    python grmc.py input.grm            # writes input.c
    python grmc.py input.grm -o out.c   # explicit output
    python grmc.py input.grm --stdout   # print to stdout

Reuses the Lexer + Parser from grm_interpreter.py (must be in same dir).
"""

import sys
import os
import re
from grm_interpreter import (
    Lexer, Parser,
    Program, StructDecl, ImplBlock, FnDecl, ExternDecl, ImportDecl,
    VarDecl, Return, ExprStmt, IfStmt, WhileStmt, Assign, Defer,
    BinOp, UnaryOp, Call, FieldAccess, MethodCall,
    Ident, Literal, FString, StructLiteral,
)


# ─────────────────────────────────────────────
# TYPE MAPPING
# ─────────────────────────────────────────────

def grm_type_to_c(t: str) -> str:
    """
    Convert a GRM type string (as encoded by parse_type) to its C equivalent.
    Encoded format: ["const "]<base_type>["&"|"*"...]
    e.g. "const Human&" -> "const Human*"
         "int"          -> "int"
         "Human&"       -> "Human*"
         "str"          -> "const char*"
    """
    is_const = t.startswith("const ")
    if is_const:
        t = t[6:]

    # strip trailing qualifiers
    is_ref = t.endswith("&") or t.endswith("*")
    base_key = t.rstrip("&* ")

    base = {
        "int":   "int",
        "float": "float",
        "bool":  "bool",
        "void":  "void",
        "str":   "const char*",
    }.get(base_key, base_key)

    if is_ref:
        prefix = "const " if is_const else ""
        return f"{prefix}{base}*"
    prefix = "const " if is_const else ""
    return f"{prefix}{base}"


# ─────────────────────────────────────────────
# F-STRING → printf / snprintf
# ─────────────────────────────────────────────

def parse_fstring(raw: str):
    """
    Parse an f-string template into (format_str, [expr_strings]).
    e.g. "{self.name}, age {self.age}" -> ("%s, age %d", ["self->name", "self->age"])
    But we don't know the types here, so we return the raw expr strings and
    let the caller substitute format specifiers.
    Returns list of ('text', str) | ('expr', str) segments.
    """
    segments = []
    i = 0
    while i < len(raw):
        if raw[i] == "{" and i + 1 < len(raw) and raw[i+1] != "{":
            depth = 1
            j = i + 1
            while j < len(raw) and depth > 0:
                if raw[j] == "{": depth += 1
                elif raw[j] == "}": depth -= 1
                j += 1
            segments.append(("expr", raw[i+1:j-1].strip()))
            i = j
        elif raw[i] == "{" and i + 1 < len(raw) and raw[i+1] == "{":
            segments.append(("text", "{"))
            i += 2
        elif raw[i] == "}" and i + 1 < len(raw) and raw[i+1] == "}":
            segments.append(("text", "}"))
            i += 2
        else:
            # accumulate text
            if segments and segments[-1][0] == "text":
                segments[-1] = ("text", segments[-1][1] + raw[i])
            else:
                segments.append(("text", raw[i]))
            i += 1
    return segments


# ─────────────────────────────────────────────
# CODEGEN
# ─────────────────────────────────────────────

class Transpiler:
    def __init__(self):
        self.struct_defs: dict[str, StructDecl] = {}  # name -> StructDecl
        self.current_struct: str | None = None         # struct we're inside
        self.indent_level = 0
        self.out: list[str] = []
        self.sym: dict[str, str] = {}                  # var name -> GRM type
        self.needs_grm_fmt = False

    # ── output helpers ──

    def emit(self, line: str = ""):
        indent = "    " * self.indent_level
        self.out.append(indent + line)

    def emit_raw(self, line: str):
        self.out.append(line)

    def result(self) -> str:
        return "\n".join(self.out)

    # ── prescan ──

    def _prescan_fstrings(self, program):
        for node in program.body:
            if isinstance(node, StructDecl):
                for m in node.methods:
                    self._scan_stmts(m.body)
            elif isinstance(node, FnDecl):
                self._scan_stmts(node.body)

    def _scan_stmts(self, stmts):
        for s in stmts:
            for attr in ('expr', 'init', 'value'):
                val = getattr(s, attr, None)
                if val is not None:
                    self._scan_expr(val)
            if hasattr(s, 'then_body'):
                self._scan_stmts(s.then_body)
                self._scan_stmts(s.else_body)
            if hasattr(s, 'body') and isinstance(getattr(s, 'body', None), list):
                self._scan_stmts(s.body)

    def _scan_expr(self, node):
        if node is None:
            return
        if isinstance(node, Call):
            callee_name = node.callee.name if isinstance(node.callee, Ident) else ""
            if callee_name not in ("print", "printf"):
                for a in node.args:
                    if isinstance(a, FString):
                        self.needs_grm_fmt = True
                        return
            for a in node.args:
                self._scan_expr(a)
        elif isinstance(node, MethodCall):
            for a in node.args:
                if isinstance(a, FString):
                    self.needs_grm_fmt = True
                    return
                self._scan_expr(a)

    # ── f-string → snprintf buffer ──

    def fstring_to_snprintf(self, raw: str, struct_name) -> str:
        segments = parse_fstring(raw)
        fmt_parts = []
        args = []
        for kind, val in segments:
            if kind == "text":
                fmt_parts.append(val.replace("\n", "\\n").replace("%", "%%"))
            else:
                c_expr, spec = self.fstring_expr_to_c(val, struct_name)
                fmt_parts.append(spec)
                args.append(c_expr)
        fmt_str = '"' + "".join(fmt_parts) + '"'
        if args:
            return f"GrmFmt({fmt_str}, {', '.join(args)})"
        return f"GrmFmt({fmt_str})"

    # ── entry ──

    def transpile(self, program: Program) -> str:
        # first pass: collect struct names so we can recognise them as types
        for node in program.body:
            if isinstance(node, StructDecl):
                self.struct_defs[node.name] = node

        self.emit("// Generated by grmc — GRM to C transpiler")
        self.emit("#include <stdio.h>")
        self.emit("#include <stdbool.h>")
        self.emit("#include <stdlib.h>")
        self.emit("#include <string.h>")

        # emit extern includes right after stdlib headers
        externs = [n for n in program.body if isinstance(n, ExternDecl)]
        if externs:
            self.emit()
            for ext in externs:
                if ext.system:
                    self.emit(f"#include <{ext.path}>")
                else:
                    self.emit(f'#include "{ext.path}"')
        self.emit()

        # prescan for f-strings used as foreign-call arguments
        self._prescan_fstrings(program)
        if self.needs_grm_fmt:
            for line in GRM_FMT_INLINE.splitlines():
                self.emit(line)
            self.emit()

        # forward-declare all structs so methods can reference them
        for name in self.struct_defs:
            self.emit(f"typedef struct {name} {name};")
        if self.struct_defs:
            self.emit()

        # emit struct bodies
        for node in program.body:
            if isinstance(node, StructDecl):
                self.emit_struct_typedef(node)

        # emit method forward declarations (so order doesn't matter)
        for node in program.body:
            if isinstance(node, StructDecl):
                for method in node.methods:
                    sig = self.method_signature(node.name, method)
                    self.emit(f"{sig};")
        if any(isinstance(n, StructDecl) for n in program.body):
            self.emit()

        # emit everything else: free functions, method bodies
        for node in program.body:
            if isinstance(node, StructDecl):
                self.emit_struct_methods(node)
            elif isinstance(node, ImplBlock):
                self.emit_impl(node)
            elif isinstance(node, FnDecl):
                self.emit_fn(node, struct_name=None)
            elif isinstance(node, ExternDecl):
                pass  # already handled above

        return self.result()

    # ── struct typedef ──

    def emit_struct_typedef(self, node: StructDecl):
        self.emit(f"struct {node.name} {{")
        self.indent_level += 1
        for (ftype, fname) in node.fields:
            self.emit(f"{self.map_field_type(ftype)} {fname};")
        self.indent_level -= 1
        self.emit("};")
        self.emit()

    def map_field_type(self, t: str) -> str:
        return grm_type_to_c(t)

    # ── method signature ──

    def method_signature(self, struct_name: str, fn: FnDecl) -> str:
        ret = grm_type_to_c(fn.ret_type)
        c_name = f"{struct_name}_{fn.name}"
        params = self.emit_params(fn.params)
        return f"{ret} {c_name}({params})"

    def emit_params(self, params: list) -> str:
        parts = []
        for (ptype, pname) in params:
            # detect const& and & refs
            c_type = self.resolve_param_type(ptype, pname, params)
            parts.append(f"{c_type} {pname}")
        return ", ".join(parts) if parts else "void"

    def resolve_param_type(self, ptype: str, pname: str, all_params: list) -> str:
        # parse_type now encodes const/ref into the type string directly,
        # so grm_type_to_c handles everything correctly.
        return grm_type_to_c(ptype)

    # ── struct methods ──

    def emit_struct_methods(self, node: StructDecl):
        self.current_struct = node.name
        for method in node.methods:
            self.emit_fn(method, struct_name=node.name)
        self.current_struct = None

    def emit_impl(self, node: ImplBlock):
        self.current_struct = node.name
        for method in node.methods:
            self.emit_fn(method, struct_name=node.name)
        self.current_struct = None

    # ── function / method body ──

    def emit_fn(self, fn: FnDecl, struct_name: str | None):
        if struct_name:
            sig = self.method_signature(struct_name, fn)
        else:
            ret = grm_type_to_c(fn.ret_type)
            params = self.emit_params(fn.params)
            sig = f"{ret} {fn.name}({params})"

        self.emit(f"{sig} {{")
        self.indent_level += 1

        # collect defers so we can emit them before every return
        defers = self.collect_defers(fn.body)
        self.emit_stmts(fn.body, defers=defers, struct_name=struct_name)

        self.indent_level -= 1
        self.emit("}")
        self.emit()

    def collect_defers(self, stmts: list) -> list:
        """Pre-scan for Defer nodes so we can emit them before returns."""
        result = []
        for s in stmts:
            if isinstance(s, Defer):
                result.append(s.expr)
        return result

    # ── statements ──

    def emit_stmts(self, stmts: list, defers: list, struct_name: str | None):
        pending_defers = []
        for stmt in stmts:
            self.emit_stmt(stmt, pending_defers, defers, struct_name)

    def emit_stmt(self, stmt, pending_defers: list, all_defers: list, struct_name: str | None):
        if isinstance(stmt, VarDecl):
            c_type = grm_type_to_c(stmt.type_str)
            base_type = stmt.type_str.lstrip("const ").rstrip("&* ")
            self.sym[stmt.name] = base_type          # track for method dispatch
            if stmt.init is None:
                self.emit(f"{c_type} {stmt.name};")
            elif isinstance(stmt.init, StructLiteral):
                fields = ", ".join(
                    f".{k} = {self.expr(v, struct_name)}"
                    for k, v in stmt.init.fields.items()
                )
                self.emit(f"{c_type} {stmt.name} = {{{fields}}};")
            else:
                self.emit(f"{c_type} {stmt.name} = {self.expr(stmt.init, struct_name)};")

        elif isinstance(stmt, Defer):
            pending_defers.append(stmt.expr)
            self.emit(f"// deferred: {self.expr(stmt.expr, struct_name)}")

        elif isinstance(stmt, ExprStmt):
            self.emit(f"{self.expr(stmt.expr, struct_name)};")

        elif isinstance(stmt, Assign):
            self.emit(f"{self.expr(stmt.target, struct_name)} = {self.expr(stmt.value, struct_name)};")

        elif isinstance(stmt, Return):
            # emit pending defers in reverse order before return
            for d in reversed(pending_defers):
                self.emit(f"{self.expr(d, struct_name)};  // deferred")
            if stmt.value is None:
                self.emit("return;")
            else:
                self.emit(f"return {self.expr(stmt.value, struct_name)};")

        elif isinstance(stmt, IfStmt):
            self.emit(f"if ({self.expr(stmt.cond, struct_name)}) {{")
            self.indent_level += 1
            self.emit_stmts(stmt.then_body, pending_defers, struct_name)
            self.indent_level -= 1
            if stmt.else_body:
                self.emit("} else {")
                self.indent_level += 1
                self.emit_stmts(stmt.else_body, pending_defers, struct_name)
                self.indent_level -= 1
            self.emit("}")

        elif isinstance(stmt, WhileStmt):
            self.emit(f"while ({self.expr(stmt.cond, struct_name)}) {{")
            self.indent_level += 1
            self.emit_stmts(stmt.body, pending_defers, struct_name)
            self.indent_level -= 1
            self.emit("}")

        else:
            raise NotImplementedError(f"Unknown stmt: {type(stmt)}")

    # ── expressions ──

    def expr(self, node, struct_name: str | None) -> str:
        if isinstance(node, Literal):
            if isinstance(node.value, bool):
                return "true" if node.value else "false"
            if isinstance(node.value, str):
                escaped = (node.value
                    .replace("\\", "\\\\")
                    .replace('"', '\\"')
                    .replace("\n", "\\n")
                    .replace("\t", "\\t")
                    .replace("\r", "\\r"))
                return f'"{escaped}"'
            if isinstance(node.value, float):
                return f"{node.value}f"
            return str(node.value)

        if isinstance(node, Ident):
            return node.name

        if isinstance(node, FString):
            return self.fstring_to_printf(node.raw, struct_name)

        if isinstance(node, BinOp):
            op = node.op
            l = self.expr(node.left, struct_name)
            r = self.expr(node.right, struct_name)
            return f"({l} {op} {r})"

        if isinstance(node, UnaryOp):
            return f"({node.op}{self.expr(node.operand, struct_name)})"

        if isinstance(node, FieldAccess):
            obj = self.expr(node.obj, struct_name)
            # if obj is 'self' we use ->, otherwise . (heuristic: self is a pointer)
            arrow = self.is_pointer_expr(node.obj)
            sep = "->" if arrow else "."
            return f"{obj}{sep}{node.field}"

        if isinstance(node, MethodCall):
            obj_expr = self.expr(node.obj, struct_name)
            obj_type = self.infer_type(node.obj, struct_name)
            c_fn = f"{obj_type}_{node.method}"
            # self arg: pass address if obj is a value, bare if already a pointer
            if self.is_pointer_expr(node.obj):
                self_arg = obj_expr
            else:
                self_arg = f"&{obj_expr}"
            extra_args = [self.expr(a, struct_name) for a in node.args]
            all_args = [self_arg] + extra_args
            return f"{c_fn}({', '.join(all_args)})"

        if isinstance(node, Call):
            callee = self.expr(node.callee, struct_name)
            # print() and printf() map to C's printf
            if callee in ("print", "printf"):
                if node.args and isinstance(node.args[0], FString):
                    return self.fstring_to_printf_call(node.args[0].raw, struct_name)
                elif node.args and isinstance(node.args[0], Literal) and isinstance(node.args[0].value, str):
                    escaped = (node.args[0].value.replace("\\","\\\\").replace('"','\\"')
                               .replace("\n","\\n").replace("\t","\\t").replace("\r","\\r"))
                    return f'printf("{escaped}")'
                else:
                    plain = [self.expr(a, struct_name) for a in node.args]
                    return f'printf("%s", {plain[0]})'
            # foreign call: f-string args → GRM_FMT stack buffer
            expanded = []
            for a in node.args:
                if isinstance(a, FString):
                    expanded.append(self.fstring_to_snprintf(a.raw, struct_name))
                else:
                    expanded.append(self.expr(a, struct_name))
            return f"{callee}({', '.join(expanded)})"

        if isinstance(node, StructLiteral):
            fields = ", ".join(
                f".{k} = {self.expr(v, struct_name)}"
                for k, v in node.fields.items()
            )
            return f"({node.type_name}){{{fields}}}"

        if isinstance(node, Assign):
            return f"{self.expr(node.target, struct_name)} = {self.expr(node.value, struct_name)}"

        raise NotImplementedError(f"Unknown expr: {type(node)}")

    # ── type inference (lightweight) ──

    def infer_type(self, node, struct_name: str | None) -> str:
        """Return the GRM base type name of an expression."""
        if isinstance(node, Ident):
            if node.name == "self" and struct_name:
                return struct_name
            # check symbol table first, then fall back to name
            if node.name in self.sym:
                return self.sym[node.name]
            return node.name
        if isinstance(node, FieldAccess):
            owner_type = self.infer_type(node.obj, struct_name)
            if owner_type in self.struct_defs:
                for (ft, fn) in self.struct_defs[owner_type].fields:
                    if fn == node.field:
                        return ft.lstrip("const ").rstrip("&* ")
        if isinstance(node, MethodCall):
            obj_type = self.infer_type(node.obj, struct_name)
            if obj_type in self.struct_defs:
                for m in self.struct_defs[obj_type].methods:
                    if m.name == node.method:
                        return m.ret_type.lstrip("const ").rstrip("&* ")
        return "unknown"

    def is_pointer_expr(self, node) -> bool:
        """True if this expression is already a pointer (i.e. use -> not .)"""
        if isinstance(node, Ident) and node.name == "self":
            return True
        return False

    # ── f-string → printf ──

    def fstring_to_printf_call(self, raw: str, struct_name: str | None) -> str:
        """Convert f-string to a full printf(...) call string."""
        segments = parse_fstring(raw)
        fmt_parts = []
        args = []

        for kind, val in segments:
            if kind == "text":
                # escape for printf
                fmt_parts.append(
                    val.replace("\\n", "\n")
                       .replace("%", "%%")
                )
            else:
                # val is an expression string — re-parse and emit it
                c_expr, specifier = self.fstring_expr_to_c(val, struct_name)
                fmt_parts.append(specifier)
                args.append(c_expr)

        fmt = "".join(fmt_parts)
        # convert \n back to literal \n in the format string
        fmt = fmt.replace("\n", "\\n")
        fmt_str = f'"{fmt}"'
        if args:
            return f"printf({fmt_str}, {', '.join(args)})"
        return f"printf({fmt_str})"

    def fstring_to_printf(self, raw: str, struct_name: str | None) -> str:
        """Same as above but returns just the call string (used from expr())."""
        return self.fstring_to_printf_call(raw, struct_name)

    def fstring_expr_to_c(self, expr_str: str, struct_name: str | None):
        """
        Parse the expression inside {}, emit C, and pick a printf specifier.
        Returns (c_expr_string, format_specifier).
        """
        tokens = Lexer(expr_str).tokenize()
        parser = Parser(tokens)
        ast = parser.parse_expr()
        c_expr = self.expr(ast, struct_name)
        specifier = self.guess_specifier(ast, struct_name)
        return c_expr, specifier

    def guess_specifier(self, node, struct_name: str | None) -> str:
        """Pick %d / %f / %s / %g based on inferred type."""
        t = self.infer_type(node, struct_name)
        if t in ("int", "bool"):
            return "%d"
        if t in ("float",):
            return "%g"     # %g drops trailing zeros, nicer for display
        if t in ("str", "const char*"):
            return "%s"
        # method call: look up return type
        if isinstance(node, MethodCall):
            obj_type = self.infer_type(node.obj, struct_name)
            if obj_type in self.struct_defs:
                for m in self.struct_defs[obj_type].methods:
                    if m.name == node.method:
                        return self.type_to_specifier(m.ret_type)
        # field access
        if isinstance(node, FieldAccess):
            owner_type = self.infer_type(node.obj, struct_name)
            if owner_type in self.struct_defs:
                for (ft, fn) in self.struct_defs[owner_type].fields:
                    if fn == node.field:
                        return self.type_to_specifier(ft)
        return "%s"  # safe fallback

    def type_to_specifier(self, t: str) -> str:
        return {
            "int":   "%d",
            "float": "%g",
            "bool":  "%d",
            "str":   "%s",
        }.get(t, "%s")


# ─────────────────────────────────────────────
# H / C SPLIT TRANSPILER
# ─────────────────────────────────────────────

STDLIB_HEADERS = [
    "#include <stdio.h>",
    "#include <stdbool.h>",
    "#include <stdlib.h>",
    "#include <string.h>",
]

# grm_runtime.h — declaration only, included by any module that uses GrmFmt
GRM_RUNTIME_H = """#ifndef GRM_RUNTIME_H
#define GRM_RUNTIME_H
#include <stdarg.h>
// GrmFmt: f-string interpolation helper.
// Single static buffer — do not use twice in the same expression.
const char* GrmFmt(const char* fmt, ...);
#endif // GRM_RUNTIME_H"""

# grm_runtime.c — compiled once, defines the function
GRM_RUNTIME_C = """// Generated by grmc — GRM runtime support
#include "grm_runtime.h"
#include <stdio.h>
const char* GrmFmt(const char* fmt, ...) {
    static char _grm_buf[256];
    va_list args;
    va_start(args, fmt);
    vsnprintf(_grm_buf, sizeof(_grm_buf), fmt, args);
    va_end(args);
    return _grm_buf;
}"""

# Single-file mode: embed the full definition inline (no separate runtime)
GRM_FMT_INLINE = """#include <stdarg.h>
// GrmFmt: f-string interpolation helper.
// Single static buffer — do not use twice in the same expression.
static const char* GrmFmt(const char* fmt, ...) {
    static char _grm_buf[256];
    va_list args;
    va_start(args, fmt);
    vsnprintf(_grm_buf, sizeof(_grm_buf), fmt, args);
    va_end(args);
    return _grm_buf;
}"""


def transpile_to_files(source: str, module_name: str, extern_structs: dict = None) -> tuple[str, str]:
    """
    Transpile GRM source into (header_src, impl_src).

    header (.h):
        - include guard
        - stdlib headers + extern headers
        - GRM_FMT macro if needed
        - import → #include "mod.h"
        - typedef struct forward decls
        - struct bodies
        - method + free-fn forward declarations

    impl (.c):
        - #include "module.h"  (self-include)
        - method bodies
        - free function bodies (including main)
    """
    tokens = Lexer(source).tokenize()
    program = Parser(tokens).parse()
    t = Transpiler()

    # seed with structs from already-transpiled modules (for cross-file type inference)
    if extern_structs:
        t.struct_defs.update(extern_structs)

    # collect struct defs + prescan
    for node in program.body:
        if isinstance(node, StructDecl):
            t.struct_defs[node.name] = node
    t._prescan_fstrings(program)

    guard = f"{module_name.upper()}_H"
    externs  = [n for n in program.body if isinstance(n, ExternDecl)]
    imports  = [n for n in program.body if isinstance(n, ImportDecl)]
    structs  = [n for n in program.body if isinstance(n, StructDecl)]
    fns      = [n for n in program.body if isinstance(n, FnDecl)]

    # ── build header ──────────────────────────────────────────────────────────
    h = t.out = []

    h.append(f"#ifndef {guard}")
    h.append(f"#define {guard}")
    h.append("")

    for line in STDLIB_HEADERS:
        h.append(line)

    if externs:
        h.append("")
        for ext in externs:
            if ext.system:
                h.append(f"#include <{ext.path}>")
            else:
                h.append(f'#include "{ext.path}"')

    if imports:
        h.append("")
        for imp in imports:
            h.append(f'#include "{imp.path}.h"')

    if t.needs_grm_fmt:
        h.append("")
        h.append('#include "grm_runtime.h"')

    h.append("")

    # typedef forward decls
    for s in structs:
        h.append(f"typedef struct {s.name} {s.name};")
    if structs:
        h.append("")

    # struct bodies
    t.indent_level = 0
    for s in structs:
        t.emit_struct_typedef(s)

    # method forward decls
    for s in structs:
        for m in s.methods:
            sig = t.method_signature(s.name, m)
            h.append(f"{sig};")
    if structs:
        h.append("")

    # free function forward decls (skip main — don't forward-declare main)
    for fn in fns:
        if fn.name != "main":
            ret = grm_type_to_c(fn.ret_type)
            params = t.emit_params(fn.params)
            h.append(f"{ret} {fn.name}({params});")
    if any(fn.name != "main" for fn in fns):
        h.append("")

    h.append(f"#endif // {guard}")
    header_src = "\n".join(h) + "\n"

    # ── build impl ────────────────────────────────────────────────────────────
    t.out = []
    t.sym = {}
    c = t.out

    c.append(f'// Generated by grmc — GRM to C transpiler')
    c.append(f'#include "{module_name}.h"')
    c.append("")

    # method bodies
    for s in structs:
        t.current_struct = s.name
        for m in s.methods:
            t.emit_fn(m, struct_name=s.name)
        t.current_struct = None

    # free function bodies
    for fn in fns:
        t.emit_fn(fn, struct_name=None)

    impl_src = "\n".join(c) + "\n"
    return header_src, impl_src, t.struct_defs


def transpile_source(source: str) -> str:
    """Single-file mode: everything in one .c (no .h). Kept for --stdout."""
    tokens = Lexer(source).tokenize()
    program = Parser(tokens).parse()
    return Transpiler().transpile(program)


# ─────────────────────────────────────────────
# CROSS-FILE STRUCT RESOLUTION
# ─────────────────────────────────────────────

def _load_known_structs(out_dir: str) -> dict:
    """
    Re-parse any .grm source files that were already transpiled into out_dir
    to extract their struct definitions for cross-file type inference.
    We find them by looking for .grm files next to existing .h outputs.
    """
    known = {}
    if not os.path.isdir(out_dir):
        return known
    for h_file in os.listdir(out_dir):
        if not h_file.endswith(".h"):
            continue
        # find the original .grm — could be in cwd or parent of out_dir
        module = h_file[:-2]
        for search_dir in [".", os.path.dirname(out_dir), out_dir]:
            grm_path = os.path.join(search_dir, f"{module}.grm")
            if os.path.isfile(grm_path):
                try:
                    with open(grm_path, encoding="utf-8") as f:
                        grm_src = f.read()
                    tokens = Lexer(grm_src).tokenize()
                    program = Parser(tokens).parse()
                    for node in program.body:
                        if isinstance(node, StructDecl):
                            known[node.name] = node
                except Exception:
                    pass
                break
    return known


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    if not args:
        print("Usage: python grmc.py <file.grm> [--stdout] [-o out.c]")
        print("       python grmc.py <file.grm> --split [-d outdir]")
        sys.exit(1)

    input_file = args[0]
    stdout_mode = "--stdout" in args
    split_mode  = "--split"  in args

    with open(input_file, "r", encoding="utf-8") as f:
        source = f.read()

    module_name = os.path.splitext(os.path.basename(input_file))[0]

    if stdout_mode:
        # single-file dump to stdout
        print(transpile_source(source))
        return

    if split_mode:
        # produce module.h + module.c
        out_dir = "."
        if "-d" in args:
            out_dir = args[args.index("-d") + 1]
        os.makedirs(out_dir, exist_ok=True)

        known_structs = _load_known_structs(out_dir)
        h_src, c_src, _ = transpile_to_files(source, module_name, known_structs)

        h_path = os.path.join(out_dir, f"{module_name}.h")
        c_path = os.path.join(out_dir, f"{module_name}.c")

        with open(h_path, "w", encoding="utf-8") as f:
            f.write(h_src)
        with open(c_path, "w", encoding="utf-8") as f:
            f.write(c_src)

        print(f"→ {h_path}")
        print(f"→ {c_path}")

        # emit grm_runtime.h/.c if this module uses GrmFmt and runtime not yet written
        rt_h = os.path.join(out_dir, "grm_runtime.h")
        rt_c = os.path.join(out_dir, "grm_runtime.c")
        if "#include \"grm_runtime.h\"" in h_src and not os.path.exists(rt_h):
            with open(rt_h, "w", encoding="utf-8") as f:
                f.write(GRM_RUNTIME_H + "\n")
            with open(rt_c, "w", encoding="utf-8") as f:
                f.write(GRM_RUNTIME_C + "\n")
            print(f"→ {rt_h}")
            print(f"→ {rt_c}")
        return

    # default: single .c output (old behaviour)
    if "-o" in args:
        output_file = args[args.index("-o") + 1]
    else:
        output_file = os.path.splitext(input_file)[0] + ".c"

    c_code = transpile_source(source)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(c_code)
    print(f"→ {output_file}")


if __name__ == "__main__":
    main()
