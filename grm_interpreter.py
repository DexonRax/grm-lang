"""
GRM Interpreter
Supports: structs (with inline methods), impl blocks, fn definitions,
f-strings, method calls, field access, print(), return, basic types.
"""

import re
import sys
from dataclasses import dataclass, field
from typing import Any, Optional


# ─────────────────────────────────────────────
# TOKEN TYPES
# ─────────────────────────────────────────────

TK_IDENT    = "IDENT"
TK_INT      = "INT"
TK_FLOAT    = "FLOAT"
TK_STRING   = "STRING"
TK_FSTRING  = "FSTRING"
TK_LBRACE   = "{"
TK_RBRACE   = "}"
TK_LPAREN   = "("
TK_RPAREN   = ")"
TK_SEMI     = ";"
TK_COMMA    = ","
TK_DOT      = "."
TK_ARROW    = "->"
TK_EQ       = "="
TK_EQEQ     = "=="
TK_NEQ      = "!="
TK_LT       = "<"
TK_GT       = ">"
TK_LTE      = "<="
TK_GTE      = ">="
TK_PLUS     = "+"
TK_MINUS    = "-"
TK_STAR     = "*"
TK_SLASH    = "/"
TK_AMP      = "&"
TK_BANG     = "!"
TK_DOT_FIELD= ".FIELD"   # .name in struct literals
TK_PLUSEQ   = "+="
TK_MINUSEQ  = "-="
TK_STAREQ   = "*="
TK_SLASHEQ  = "/="
TK_PLUSPLUS = "++"
TK_MINUSMINUS = "--"
TK_LBRACKET = "["
TK_RBRACKET = "]"
TK_QUESTION = "?"
TK_COLON    = ":"
TK_EOF      = "EOF"

KEYWORDS = {"struct", "impl", "fn", "return", "if", "else", "while", "for",
            "int", "float", "bool", "void", "true", "false", "const", "defer", "extern", "import", "sizeof"}


@dataclass
class Token:
    kind: str
    value: Any
    line: int


# ─────────────────────────────────────────────
# LEXER
# ─────────────────────────────────────────────

class Lexer:
    def __init__(self, src: str):
        self.src = src
        self.pos = 0
        self.line = 1
        self.tokens: list[Token] = []

    def error(self, msg):
        raise SyntaxError(f"[Lexer] Line {self.line}: {msg}")

    def peek(self, offset=0):
        p = self.pos + offset
        return self.src[p] if p < len(self.src) else "\0"

    def advance(self):
        ch = self.src[self.pos]
        self.pos += 1
        if ch == "\n":
            self.line += 1
        return ch

    def match(self, ch):
        if self.peek() == ch:
            self.advance()
            return True
        return False

    def skip_whitespace_and_comments(self):
        while self.pos < len(self.src):
            ch = self.peek()
            if ch in " \t\r\n":
                self.advance()
            elif ch == "/" and self.peek(1) == "/":
                while self.pos < len(self.src) and self.peek() != "\n":
                    self.advance()
            elif ch == "/" and self.peek(1) == "*":
                self.advance(); self.advance()
                while self.pos < len(self.src):
                    if self.peek() == "*" and self.peek(1) == "/":
                        self.advance(); self.advance()
                        break
                    self.advance()
            else:
                break

    def read_string(self, quote, fstring=False):
        result = []
        while self.pos < len(self.src):
            ch = self.peek()
            if ch == "\\":
                self.advance()
                esc = self.advance()
                result.append({"n": "\n", "t": "\t", "r": "\r",
                               "\\": "\\", '"': '"', "'": "'"}.get(esc, esc))
            elif ch == quote:
                self.advance()
                break
            else:
                result.append(self.advance())
        return "".join(result)

    def tokenize(self) -> list[Token]:
        while self.pos < len(self.src):
            self.skip_whitespace_and_comments()
            if self.pos >= len(self.src):
                break

            line = self.line
            ch = self.peek()

            # f-string
            if ch == "f" and self.peek(1) in ('"', "'"):
                self.advance()
                quote = self.advance()
                raw = self.read_string(quote, fstring=True)
                self.tokens.append(Token(TK_FSTRING, raw, line))
                continue

            # regular string
            if ch in ('"', "'"):
                self.advance()
                raw = self.read_string(ch)
                self.tokens.append(Token(TK_STRING, raw, line))
                continue

            # numbers: handles 123, 1.5, 1.f, 0.f, .5f
            # NOTE: "." only starts a number if followed by a DIGIT (not f/F alone)
            #       to avoid eating field access like self.fc, self.fs
            if ch.isdigit() or (ch == "." and self.peek(1).isdigit()):
                num = []
                is_float = False
                while self.peek().isdigit():
                    num.append(self.advance())
                if self.peek() == "." and (self.peek(1).isdigit() or self.peek(1) in ("f","F")):
                    is_float = True
                    num.append(self.advance())   # the dot
                    while self.peek().isdigit():
                        num.append(self.advance())
                if self.peek() in ("f", "F"):
                    self.advance()
                    is_float = True
                raw = "".join(num) or "0"
                val = float(raw) if is_float else int(raw)
                self.tokens.append(Token(TK_FLOAT if is_float else TK_INT, val, line))
                continue

            # identifiers / keywords
            if ch.isalpha() or ch == "_":
                word = []
                while self.peek().isalpha() or self.peek().isdigit() or self.peek() == "_":
                    word.append(self.advance())
                word = "".join(word)
                kind = word if word in KEYWORDS else TK_IDENT
                if word == "true":
                    self.tokens.append(Token(TK_IDENT, True, line))
                elif word == "false":
                    self.tokens.append(Token(TK_IDENT, False, line))
                else:
                    self.tokens.append(Token(kind, word, line))
                continue

            # two-char ops
            if ch == "-" and self.peek(1) == ">":
                self.advance(); self.advance()
                self.tokens.append(Token(TK_ARROW, "->", line))
                continue
            if ch == "=" and self.peek(1) == "=":
                self.advance(); self.advance()
                self.tokens.append(Token(TK_EQEQ, "==", line))
                continue
            if ch == "!" and self.peek(1) == "=":
                self.advance(); self.advance()
                self.tokens.append(Token(TK_NEQ, "!=", line))
                continue
            if ch == "<" and self.peek(1) == "=":
                self.advance(); self.advance()
                self.tokens.append(Token(TK_LTE, "<=", line))
                continue
            if ch == ">" and self.peek(1) == "=":
                self.advance(); self.advance()
                self.tokens.append(Token(TK_GTE, ">=", line))
                continue
            if ch == "+" and self.peek(1) == "+":
                self.advance(); self.advance()
                self.tokens.append(Token(TK_PLUSPLUS, "++", line))
                continue
            if ch == "-" and self.peek(1) == "-":
                self.advance(); self.advance()
                self.tokens.append(Token(TK_MINUSMINUS, "--", line))
                continue
            if ch == "+" and self.peek(1) == "=":
                self.advance(); self.advance()
                self.tokens.append(Token(TK_PLUSEQ, "+=", line))
                continue
            if ch == "-" and self.peek(1) == "=":
                self.advance(); self.advance()
                self.tokens.append(Token(TK_MINUSEQ, "-=", line))
                continue
            if ch == "*" and self.peek(1) == "=":
                self.advance(); self.advance()
                self.tokens.append(Token(TK_STAREQ, "*=", line))
                continue
            if ch == "/" and self.peek(1) == "=":
                self.advance(); self.advance()
                self.tokens.append(Token(TK_SLASHEQ, "/=", line))
                continue

            # single-char tokens
            simple = {
                "{": TK_LBRACE, "}": TK_RBRACE,
                "(": TK_LPAREN, ")": TK_RPAREN,
                ";": TK_SEMI, ",": TK_COMMA,
                ".": TK_DOT,   "=": TK_EQ,
                "<": TK_LT,    ">": TK_GT,
                "+": TK_PLUS,  "-": TK_MINUS,
                "*": TK_STAR,  "/": TK_SLASH,
                "&": TK_AMP,   "!": TK_BANG,
                "[": TK_LBRACKET, "]": TK_RBRACKET,
                "?": TK_QUESTION, ":": TK_COLON,
            }
            if ch in simple:
                self.tokens.append(Token(simple[ch], ch, line))
                self.advance()
                continue

            self.error(f"Unknown character: {ch!r}")

        self.tokens.append(Token(TK_EOF, None, self.line))
        return self.tokens


# ─────────────────────────────────────────────
# AST NODES
# ─────────────────────────────────────────────

@dataclass
class Program:
    body: list

@dataclass
class ImportDecl:
    path: str          # module name, e.g. "human" → #include "human.h"

@dataclass
class ExternDecl:
    path: str          # raw include path, e.g. "raylib.h" or <raylib.h>
    system: bool       # True = <...>, False = "..."

@dataclass
class StructDecl:
    name: str
    fields: list          # list of (type_str, name)
    methods: list         # list of FnDecl

@dataclass
class ImplBlock:
    name: str
    methods: list

@dataclass
class FnDecl:
    ret_type: str
    name: str
    params: list          # list of (type_str, name)
    body: list            # list of statements

@dataclass
class VarDecl:
    type_str: str
    name: str
    init: Any

@dataclass
class StructLiteral:
    type_name: str
    fields: dict          # {name: expr}

@dataclass
class CastLiteral:
    type_name: str    # e.g. "Vector2"
    values: list      # positional values

@dataclass
class PositionalLiteral:
    values: list    # [expr, expr, ...] — positional, no field names

@dataclass
class AnonStructLiteral:
    fields: dict          # {name: expr} — type inferred from context

@dataclass
class Assign:
    target: Any
    value: Any

@dataclass
class Return:
    value: Any

@dataclass
class ExprStmt:
    expr: Any

@dataclass
class IfStmt:
    cond: Any
    then_body: list
    else_body: list

@dataclass
class ForStmt:
    init: Any       # VarDecl or ExprStmt or None
    cond: Any       # expr or None
    step: Any       # expr or None
    body: list

@dataclass
class WhileStmt:
    cond: Any
    body: list

@dataclass
class Defer:
    expr: Any

@dataclass
class Ternary:
    cond: Any
    then: Any
    else_: Any

@dataclass
class BinOp:
    op: str
    left: Any
    right: Any

@dataclass
class UnaryOp:
    op: str
    operand: Any

@dataclass
class Call:
    callee: Any           # expr that evaluates to callable
    args: list

@dataclass
class IndexAccess:
    obj: Any
    index: Any

@dataclass
class FieldAccess:
    obj: Any
    field: str

@dataclass
class MethodCall:
    obj: Any
    method: str
    args: list

@dataclass
class Ident:
    name: str

@dataclass
class Literal:
    value: Any

@dataclass
class SizeOf:
    type_str: str   # raw content of sizeof(...), passed through verbatim

@dataclass
class FString:
    raw: str              # raw template text


# ─────────────────────────────────────────────
# PARSER
# ─────────────────────────────────────────────

class Parser:
    def __init__(self, tokens: list[Token]):
        self.tokens = tokens
        self.pos = 0

    def error(self, msg):
        tok = self.current()
        raise SyntaxError(f"[Parser] Line {tok.line}: {msg} (got {tok.kind!r} = {tok.value!r})")

    def current(self) -> Token:
        return self.tokens[self.pos]

    def peek(self, offset=1) -> Token:
        p = self.pos + offset
        return self.tokens[p] if p < len(self.tokens) else self.tokens[-1]

    def advance(self) -> Token:
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def expect(self, kind) -> Token:
        tok = self.current()
        if tok.kind != kind:
            self.error(f"Expected {kind!r}")
        return self.advance()

    def match(self, *kinds) -> bool:
        return self.current().kind in kinds

    # ── top level ──

    def parse(self) -> Program:
        body = []
        while not self.match(TK_EOF):
            body.append(self.parse_top())
        return Program(body)

    def parse_top(self):
        if self.match("import"):
            return self.parse_import()
        if self.match("extern"):
            return self.parse_extern()
        if self.match("struct"):
            return self.parse_struct()
        if self.match("impl"):
            return self.parse_impl()
        if self.match("fn"):
            return self.parse_fn()
        # global var decl
        if self.is_type():
            return self.parse_var_decl()
        self.error("Unexpected token at top level")

    # ── import ──

    def parse_import(self):
        self.expect("import")
        tok = self.current()
        if tok.kind == TK_STRING:
            path = tok.value
            self.advance()
            self.expect(TK_SEMI)
            return ImportDecl(path)
        self.error("Expected string path after import")

    # ── extern ──

    def parse_extern(self):
        self.expect("extern")
        tok = self.current()
        # extern "raylib.h";  or  extern <raylib.h>;
        if tok.kind == TK_STRING:
            path = tok.value
            self.advance()
            self.expect(TK_SEMI)
            return ExternDecl(path, system=False)
        elif tok.kind == TK_LT:
            self.advance()
            # collect everything until >
            parts = []
            while not self.match(TK_GT) and not self.match(TK_EOF):
                parts.append(str(self.current().value))
                self.advance()
            self.expect(TK_GT)
            self.expect(TK_SEMI)
            return ExternDecl("".join(parts), system=True)
        else:
            self.error("Expected string or <path> after extern")

    # ── struct ──

    def parse_struct(self):
        self.expect("struct")
        name = self.expect(TK_IDENT).value
        self.expect(TK_LBRACE)
        fields = []
        methods = []
        while not self.match(TK_RBRACE):
            if self.match("fn"):
                methods.append(self.parse_fn())
            elif self.is_type():
                t = self.parse_type()
                n = self.expect(TK_IDENT).value
                default = None
                if self.match(TK_EQ):
                    # field default value — consumed and stored for future use
                    self.advance()
                    default = self.parse_expr()
                self.expect(TK_SEMI)
                fields.append((t, n, default))
            else:
                self.error("Expected field or method in struct")
        self.expect(TK_RBRACE)
        return StructDecl(name, fields, methods)

    def parse_impl(self):
        self.expect("impl")
        name = self.expect(TK_IDENT).value
        self.expect(TK_LBRACE)
        methods = []
        while not self.match(TK_RBRACE):
            if self.match("fn"):
                methods.append(self.parse_fn())
            else:
                self.error("Expected method in impl block")
        self.expect(TK_RBRACE)
        return ImplBlock(name, methods)

    # ── function ──

    def parse_fn(self):
        self.expect("fn")
        ret = self.parse_type()
        name = self.expect(TK_IDENT).value
        self.expect(TK_LPAREN)
        params = []
        while not self.match(TK_RPAREN):
            t = self.parse_type()
            n = self.expect(TK_IDENT).value
            params.append((t, n))
            if self.match(TK_COMMA):
                self.advance()
        self.expect(TK_RPAREN)
        body = self.parse_block()
        return FnDecl(ret, name, params, body)

    def parse_block(self):
        self.expect(TK_LBRACE)
        stmts = []
        while not self.match(TK_RBRACE):
            stmts.append(self.parse_stmt())
        self.expect(TK_RBRACE)
        return stmts

    # ── statements ──

    def parse_stmt(self):
        if self.match("return"):
            return self.parse_return()
        if self.match("if"):
            return self.parse_if()
        if self.match("for"):
            return self.parse_for()
        if self.match("while"):
            return self.parse_while()
        if self.match("defer"):
            return self.parse_defer()
        if self.is_type() and self.peek().kind == TK_IDENT:
            return self.parse_var_decl()
        return self.parse_expr_stmt()

    def parse_return(self):
        self.expect("return")
        val = None
        if not self.match(TK_SEMI):
            val = self.parse_expr()
        self.expect(TK_SEMI)
        return Return(val)

    def parse_if(self):
        self.expect("if")
        self.expect(TK_LPAREN)
        cond = self.parse_expr()
        self.expect(TK_RPAREN)
        then_body = self.parse_block()
        else_body = []
        if self.match("else"):
            self.advance()
            if self.match("if"):
                else_body = [self.parse_if()]
            else:
                else_body = self.parse_block()
        return IfStmt(cond, then_body, else_body)

    def parse_for(self):
        self.expect("for")
        self.expect(TK_LPAREN)
        # init: var decl or expr or empty
        if self.match(TK_SEMI):
            init = None
            self.advance()
        elif self.is_type() and self.peek().kind == TK_IDENT:
            init = self.parse_var_decl()   # consumes semicolon
        else:
            init = ExprStmt(self.parse_expr())
            self.expect(TK_SEMI)
        # cond
        if self.match(TK_SEMI):
            cond = None
            self.advance()
        else:
            cond = self.parse_expr()
            self.expect(TK_SEMI)
        # step
        if self.match(TK_RPAREN):
            step = None
        else:
            step = self.parse_expr()
        self.expect(TK_RPAREN)
        body = self.parse_block()
        return ForStmt(init, cond, step, body)

    def parse_while(self):
        self.expect("while")
        self.expect(TK_LPAREN)
        cond = self.parse_expr()
        self.expect(TK_RPAREN)
        body = self.parse_block()
        return WhileStmt(cond, body)

    def parse_defer(self):
        self.expect("defer")
        expr = self.parse_expr()
        self.expect(TK_SEMI)
        return Defer(expr)

    def parse_var_decl(self):
        t = self.parse_type()
        name = self.expect(TK_IDENT).value
        init = None
        if self.match(TK_EQ):
            self.advance()
            # struct literal: TypeName = { .field = val, ... }
            if self.match(TK_LBRACE):
                init = self.parse_struct_literal(t)
            else:
                init = self.parse_expr()
        self.expect(TK_SEMI)
        return VarDecl(t, name, init)

    def parse_struct_literal(self, type_name):
        self.expect(TK_LBRACE)
        fields = {}
        while not self.match(TK_RBRACE):
            self.expect(TK_DOT)
            fname = self.expect(TK_IDENT).value
            self.expect(TK_EQ)
            val = self.parse_expr()
            fields[fname] = val
            if self.match(TK_COMMA):
                self.advance()
        self.expect(TK_RBRACE)
        return StructLiteral(type_name, fields)

    def parse_expr_stmt(self):
        expr = self.parse_expr()
        self.expect(TK_SEMI)
        return ExprStmt(expr)

    # ── expressions ──

    def parse_expr(self):
        return self.parse_assign()

    def parse_assign(self):
        left = self.parse_ternary()
        if self.current().kind in (TK_EQ, TK_PLUSEQ, TK_MINUSEQ, TK_STAREQ, TK_SLASHEQ):
            compound = {TK_PLUSEQ: "+", TK_MINUSEQ: "-", TK_STAREQ: "*", TK_SLASHEQ: "/"}
            tok = self.advance()
            right = self.parse_assign()
            if tok.kind == TK_EQ:
                return Assign(left, right)
            return Assign(left, BinOp(compound[tok.kind], left, right))
        return left

    def parse_ternary(self):
        cond = self.parse_comparison()
        if self.match(TK_QUESTION):
            self.advance()
            then = self.parse_expr()
            self.expect(TK_COLON)
            else_ = self.parse_ternary()  # right-associative
            return Ternary(cond, then, else_)
        return cond



    def parse_comparison(self):
        left = self.parse_additive()
        while self.match(TK_EQEQ, TK_NEQ, TK_LT, TK_GT, TK_LTE, TK_GTE):
            op = self.advance().kind
            right = self.parse_additive()
            left = BinOp(op, left, right)
        return left

    def parse_additive(self):
        left = self.parse_multiplicative()
        while self.match(TK_PLUS, TK_MINUS):
            op = self.advance().kind
            right = self.parse_multiplicative()
            left = BinOp(op, left, right)
        return left

    def parse_multiplicative(self):
        left = self.parse_unary()
        while self.match(TK_STAR, TK_SLASH):
            op = self.advance().kind
            right = self.parse_unary()
            left = BinOp(op, left, right)
        return left

    def parse_unary(self):
        if self.match(TK_PLUSPLUS):
            self.advance()
            operand = self.parse_unary()
            return Assign(operand, BinOp("+", operand, Literal(1)))
        if self.match(TK_MINUSMINUS):
            self.advance()
            operand = self.parse_unary()
            return Assign(operand, BinOp("-", operand, Literal(1)))
        if self.match(TK_BANG):
            self.advance()
            return UnaryOp("!", self.parse_unary())
        if self.match(TK_MINUS):
            self.advance()
            return UnaryOp("-", self.parse_unary())
        if self.match(TK_AMP):
            self.advance()
            return UnaryOp("&", self.parse_unary())
        if self.match(TK_STAR):
            self.advance()
            return UnaryOp("*", self.parse_unary())
        return self.parse_postfix()

    def parse_postfix(self):
        expr = self.parse_primary()
        while True:
            if self.match(TK_LBRACKET):
                self.advance()
                index = self.parse_expr()
                self.expect(TK_RBRACKET)
                expr = IndexAccess(expr, index)
                continue
            if self.match(TK_PLUSPLUS):
                self.advance()
                expr = Assign(expr, BinOp("+", expr, Literal(1)))
                break  # postfix inc/dec can't chain
            if self.match(TK_MINUSMINUS):
                self.advance()
                expr = Assign(expr, BinOp("-", expr, Literal(1)))
                break
            if self.match(TK_DOT):
                self.advance()
                name = self.expect(TK_IDENT).value
                if self.match(TK_LPAREN):
                    self.advance()
                    args = self.parse_args()
                    self.expect(TK_RPAREN)
                    expr = MethodCall(expr, name, args)
                else:
                    expr = FieldAccess(expr, name)
            elif self.match(TK_ARROW):
                self.advance()
                name = self.expect(TK_IDENT).value
                if self.match(TK_LPAREN):
                    self.advance()
                    args = self.parse_args()
                    self.expect(TK_RPAREN)
                    expr = MethodCall(expr, name, args)
                else:
                    expr = FieldAccess(expr, name)
            elif self.match(TK_LPAREN):
                self.advance()
                args = self.parse_args()
                self.expect(TK_RPAREN)
                expr = Call(expr, args)
            else:
                break
        return expr

    def parse_args(self):
        args = []
        while not self.match(TK_RPAREN):
            args.append(self.parse_expr())
            if self.match(TK_COMMA):
                self.advance()
        return args

    def parse_primary(self):
        tok = self.current()

        if tok.kind == TK_INT:
            self.advance()
            return Literal(tok.value)
        if tok.kind == TK_FLOAT:
            self.advance()
            return Literal(tok.value)
        if tok.kind == TK_STRING:
            self.advance()
            return Literal(tok.value)
        if tok.kind == TK_FSTRING:
            self.advance()
            return FString(tok.value)
        if tok.kind == TK_IDENT:
            self.advance()
            if isinstance(tok.value, bool):
                return Literal(tok.value)
            return Ident(tok.value)
        if tok.kind in KEYWORDS and tok.kind not in ("fn", "struct", "impl",
                                                       "return", "if", "else",
                                                       "while", "for"):
            self.advance()
            return Ident(tok.value)
        if tok.kind == "sizeof":
            self.advance()
            self.expect(TK_LPAREN)
            # sizeof can take a type name or an expression — we treat both as opaque
            inner_tokens = []
            depth = 1
            while depth > 0 and not self.match(TK_EOF):
                t = self.current()
                if t.kind == TK_LPAREN: depth += 1
                elif t.kind == TK_RPAREN:
                    depth -= 1
                    if depth == 0:
                        break
                inner_tokens.append(t.value if t.value is not None else t.kind)
                self.advance()
            self.expect(TK_RPAREN)
            return SizeOf("".join(str(x) for x in inner_tokens))
        if tok.kind == TK_LBRACE:
            next_tok = self.peek(1)
            if next_tok.kind == TK_DOT or next_tok.kind == TK_RBRACE:
                # designated: { .field = val, ... }
                self.advance()  # consume {
                fields = {}
                while not self.match(TK_RBRACE):
                    self.expect(TK_DOT)
                    fname = self.expect(TK_IDENT).value
                    self.expect(TK_EQ)
                    val = self.parse_expr()
                    fields[fname] = val
                    if self.match(TK_COMMA):
                        self.advance()
                self.expect(TK_RBRACE)
                return AnonStructLiteral(fields)
            else:
                # positional: { expr, expr, ... }
                self.advance()  # consume {
                values = []
                while not self.match(TK_RBRACE):
                    values.append(self.parse_expr())
                    if self.match(TK_COMMA):
                        self.advance()
                self.expect(TK_RBRACE)
                return PositionalLiteral(values)
        if tok.kind == TK_LPAREN:
            self.advance()
            # check for compound literal cast: (TypeName){ ... }
            if self.is_type() and self.peek().kind == TK_RPAREN:
                type_name = self.parse_type()
                self.expect(TK_RPAREN)
                if self.match(TK_LBRACE):
                    self.advance()
                    values = []
                    while not self.match(TK_RBRACE):
                        values.append(self.parse_expr())
                        if self.match(TK_COMMA):
                            self.advance()
                    self.expect(TK_RBRACE)
                    return CastLiteral(type_name, values)
                # not a compound literal, just a cast expression — put type back as ident
                return Ident(type_name)
            expr = self.parse_expr()
            self.expect(TK_RPAREN)
            return expr

        self.error(f"Unexpected token in expression: {tok.kind!r} = {tok.value!r}")

    # ── type helpers ──

    def is_type(self):
        k = self.current().kind
        v = self.current().value
        return k in ("int", "float", "bool", "void") or (k == TK_IDENT and isinstance(v, str))

    def parse_type(self):
        # optional const prefix (consumed, stored as part of type string)
        is_const = False
        if self.current().kind == "const":
            self.advance()
            is_const = True
        tok = self.current()
        if tok.kind in ("int", "float", "bool", "void"):
            self.advance()
            t = tok.kind
        elif tok.kind == TK_IDENT:
            self.advance()
            t = tok.value
        else:
            self.error("Expected type")
        # collect ref/pointer qualifiers
        qualifiers = []
        while self.match(TK_AMP, TK_STAR):
            qualifiers.append(self.advance().kind)
        # encode const+ref info so the transpiler can reconstruct it
        if is_const:
            t = "const " + t
        if qualifiers:
            t = t + "".join(qualifiers)
        return t


# ─────────────────────────────────────────────
# RUNTIME VALUES
# ─────────────────────────────────────────────

class GRMInstance:
    """Runtime instance of a GRM struct."""
    def __init__(self, type_name: str, struct_def):
        self.type_name = type_name
        self.struct_def = struct_def
        self.fields: dict = {}

    def __repr__(self):
        return f"{self.type_name}{{{', '.join(f'{k}={v}' for k, v in self.fields.items())}}}"


class GRMFunction:
    def __init__(self, decl: FnDecl, closure: dict):
        self.decl = decl
        self.closure = closure


class ReturnException(Exception):
    def __init__(self, value):
        self.value = value


# ─────────────────────────────────────────────
# INTERPRETER
# ─────────────────────────────────────────────

class Interpreter:
    def __init__(self):
        self.globals: dict = {}
        self.struct_defs: dict = {}   # name -> StructDecl
        self.methods: dict = {}       # struct_name -> {method_name -> FnDecl}

        # built-ins
        self.globals["print"] = self._builtin_print

    # ── built-ins ──

    def _builtin_print(self, *args):
        text = "".join(str(a) for a in args)
        # handle \n literally if it appears as two chars
        sys.stdout.write(text.replace("\\n", "\n"))
        return None

    # ── execution entry ──

    def run(self, program: Program):
        # first pass: register structs, impls, functions
        for node in program.body:
            if isinstance(node, StructDecl):
                self.struct_defs[node.name] = node
                self.methods.setdefault(node.name, {})
                for m in node.methods:
                    self.methods[node.name][m.name] = m
            elif isinstance(node, ImplBlock):
                self.methods.setdefault(node.name, {})
                for m in node.methods:
                    self.methods[node.name][m.name] = m
            elif isinstance(node, FnDecl):
                self.globals[node.name] = GRMFunction(node, self.globals.copy())

        # second pass: call main
        if "main" not in self.globals:
            raise RuntimeError("No main() function found")
        self.call_function(self.globals["main"], [])

    # ── statement execution ──

    def exec_block(self, stmts: list, env: dict):
        for stmt in stmts:
            self.exec_stmt(stmt, env)

    def exec_stmt(self, stmt, env: dict):
        if isinstance(stmt, VarDecl):
            val = self.eval_expr(stmt.init, env) if stmt.init is not None else None
            env[stmt.name] = val

        elif isinstance(stmt, Assign):
            val = self.eval_expr(stmt.value, env)
            self.do_assign(stmt.target, val, env)

        elif isinstance(stmt, Return):
            val = self.eval_expr(stmt.value, env) if stmt.value is not None else None
            raise ReturnException(val)

        elif isinstance(stmt, ExprStmt):
            self.eval_expr(stmt.expr, env)

        elif isinstance(stmt, IfStmt):
            cond = self.eval_expr(stmt.cond, env)
            if cond:
                self.exec_block(stmt.then_body, env)
            else:
                self.exec_block(stmt.else_body, env)

        elif isinstance(stmt, WhileStmt):
            while self.eval_expr(stmt.cond, env):
                self.exec_block(stmt.body, env)

        elif isinstance(stmt, ForStmt):
            if stmt.init is not None:
                self.exec_stmt(stmt.init, env)
            while (stmt.cond is None or self.eval_expr(stmt.cond, env)):
                self.exec_block(stmt.body, env)
                if stmt.step is not None:
                    self.eval_expr(stmt.step, env)

        else:
            raise RuntimeError(f"Unknown statement: {type(stmt)}")

    def do_assign(self, target, val, env):
        if isinstance(target, Ident):
            env[target.name] = val
        elif isinstance(target, FieldAccess):
            obj = self.eval_expr(target.obj, env)
            if isinstance(obj, GRMInstance):
                obj.fields[target.field] = val
            else:
                raise RuntimeError(f"Cannot assign field on {obj!r}")
        elif isinstance(target, IndexAccess):
            obj = self.eval_expr(target.obj, env)
            idx = self.eval_expr(target.index, env)
            obj[idx] = val
        else:
            raise RuntimeError(f"Cannot assign to {target!r}")

    # ── expression evaluation ──

    def eval_expr(self, expr, env: dict):
        if expr is None:
            return None

        if isinstance(expr, Literal):
            return expr.value

        if isinstance(expr, Ident):
            name = expr.name
            if name in env:
                return env[name]
            if name in self.globals:
                return self.globals[name]
            raise NameError(f"Undefined name: {name!r}")

        if isinstance(expr, FString):
            return self.eval_fstring(expr.raw, env)

        if isinstance(expr, BinOp):
            l = self.eval_expr(expr.left, env)
            r = self.eval_expr(expr.right, env)
            return self.apply_binop(expr.op, l, r)

        if isinstance(expr, UnaryOp):
            v = self.eval_expr(expr.operand, env)
            if expr.op == "-":
                return -v
            if expr.op == "!":
                return not v

        if isinstance(expr, IndexAccess):
            obj = self.eval_expr(expr.obj, env)
            idx = self.eval_expr(expr.index, env)
            return obj[idx]

        if isinstance(expr, SizeOf):
            # interpreter: return a placeholder (sizeof is a compile-time C concept)
            return 0

        if isinstance(expr, FieldAccess):
            obj = self.eval_expr(expr.obj, env)
            if isinstance(obj, GRMInstance):
                if expr.field in obj.fields:
                    return obj.fields[expr.field]
                raise AttributeError(f"No field {expr.field!r} on {obj.type_name}")
            raise RuntimeError(f"Field access on non-struct {obj!r}")

        if isinstance(expr, MethodCall):
            obj = self.eval_expr(expr.obj, env)
            args = [self.eval_expr(a, env) for a in expr.args]
            return self.call_method(obj, expr.method, args)

        if isinstance(expr, Call):
            callee = self.eval_expr(expr.callee, env)
            args = [self.eval_expr(a, env) for a in expr.args]
            if callable(callee):
                return callee(*args)
            if isinstance(callee, GRMFunction):
                return self.call_function(callee, args)
            raise RuntimeError(f"Not callable: {callee!r}")

        if isinstance(expr, StructLiteral):
            if expr.type_name not in self.struct_defs:
                raise RuntimeError(f"Unknown struct type: {expr.type_name}")
            inst = GRMInstance(expr.type_name, self.struct_defs[expr.type_name])
            for fname, fexpr in expr.fields.items():
                inst.fields[fname] = self.eval_expr(fexpr, env)
            return inst

        if isinstance(expr, Ternary):
            return self.eval_expr(expr.then if self.eval_expr(expr.cond, env) else expr.else_, env)

        if isinstance(expr, AnonStructLiteral):
            return {k: self.eval_expr(v, env) for k, v in expr.fields.items()}

        if isinstance(expr, CastLiteral):
            return [self.eval_expr(v, env) for v in expr.values]

        if isinstance(expr, PositionalLiteral):
            return [self.eval_expr(v, env) for v in expr.values]

        if isinstance(expr, Assign):
            val = self.eval_expr(expr.value, env)
            self.do_assign(expr.target, val, env)
            return val

        raise RuntimeError(f"Unknown expression type: {type(expr)}")

    def apply_binop(self, op, l, r):
        ops = {
            "+": lambda a, b: a + b,
            "-": lambda a, b: a - b,
            "*": lambda a, b: a * b,
            "/": lambda a, b: a / b if isinstance(a, float) or isinstance(b, float) else a // b,
            "==": lambda a, b: a == b,
            "!=": lambda a, b: a != b,
            "<":  lambda a, b: a < b,
            ">":  lambda a, b: a > b,
            "<=": lambda a, b: a <= b,
            ">=": lambda a, b: a >= b,
        }
        if op not in ops:
            raise RuntimeError(f"Unknown operator: {op!r}")
        return ops[op](l, r)

    # ── method dispatch ──

    def call_method(self, obj, method_name, args):
        if isinstance(obj, GRMInstance):
            type_name = obj.type_name
            if type_name in self.methods and method_name in self.methods[type_name]:
                fn_decl = self.methods[type_name][method_name]
                fn = GRMFunction(fn_decl, self.globals.copy())
                return self.call_function(fn, [obj] + args)
            raise AttributeError(f"No method {method_name!r} on {type_name}")
        raise RuntimeError(f"Method call on non-struct: {obj!r}")

    # ── function call ──

    def call_function(self, fn: GRMFunction, args: list):
        decl = fn.decl
        env = dict(fn.closure)

        # bind parameters (skip &-ref markers, they're transparent at runtime)
        params = decl.params
        if len(args) != len(params):
            raise RuntimeError(
                f"Function {decl.name!r}: expected {len(params)} args, got {len(args)}")
        for (ptype, pname), val in zip(params, args):
            env[pname] = val

        # also expose global functions so recursive / cross-calls work
        for k, v in self.globals.items():
            if k not in env:
                env[k] = v

        try:
            self.exec_block(decl.body, env)
        except ReturnException as ret:
            return ret.value
        return None

    # ── f-string interpolation ──

    def eval_fstring(self, raw: str, env: dict) -> str:
        result = []
        i = 0
        while i < len(raw):
            if raw[i] == "{" and i + 1 < len(raw) and raw[i+1] != "{":
                # find matching }
                depth = 1
                j = i + 1
                while j < len(raw) and depth > 0:
                    if raw[j] == "{":
                        depth += 1
                    elif raw[j] == "}":
                        depth -= 1
                    j += 1
                inner = raw[i+1:j-1].strip()
                val = self.eval_fstring_expr(inner, env)
                result.append(str(val) if not isinstance(val, float) else
                               (str(int(val)) if val == int(val) else str(val)))
                i = j
            elif raw[i] == "{" and i + 1 < len(raw) and raw[i+1] == "{":
                result.append("{")
                i += 2
            elif raw[i] == "}" and i + 1 < len(raw) and raw[i+1] == "}":
                result.append("}")
                i += 2
            elif raw[i] == "\\" and i + 1 < len(raw):
                esc = raw[i+1]
                result.append({"n": "\n", "t": "\t", "r": "\r"}.get(esc, esc))
                i += 2
            else:
                result.append(raw[i])
                i += 1
        return "".join(result)

    def eval_fstring_expr(self, expr_str: str, env: dict):
        """Parse and evaluate a small expression string inside f-string {}."""
        try:
            tokens = Lexer(expr_str).tokenize()
            parser = Parser(tokens)
            expr = parser.parse_expr()
            return self.eval_expr(expr, env)
        except Exception as e:
            raise RuntimeError(f"Error in f-string expression {{{expr_str!r}}}: {e}")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def run_grm(code: str):
    tokens = Lexer(code).tokenize()
    program = Parser(tokens).parse()
    interp = Interpreter()
    interp.run(program)


if __name__ == "__main__":
    code = """
struct Human{
    int age;
    float height;

    fn int getAge(Human& self){
        return self.age;
    }
}

impl Human{
    fn float getHeight(Human& self){
        return self.height;
    }
}

fn int main(){

    Human john = { .age = 10, .height = 175.5f};

    print(f"John's age: {john.getAge()}\n");
    print(f"John's height: {john.getHeight()}\n");
    
    return 0;
}
"""
    run_grm(code)
