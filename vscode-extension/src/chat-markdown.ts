/** Parse replies to a small display tree; model HTML never becomes DOM HTML. */
import { lexer, type Token, type Tokens } from "marked";

export interface TextNode {
  tag?: string;
  text?: string;
  href?: string;
  children?: TextNode[];
}

export function markdown(text: string): TextNode[] {
  const convert = (tokens: Token[]): TextNode[] => tokens.flatMap((token): TextNode[] => {
    const children = () => convert("tokens" in token ? token.tokens ?? [] : []);
    switch (token.type) {
      case "space": return [];
      case "paragraph": return [{ tag: "p", children: children() }];
      case "heading": return [{ tag: `h${Math.min(token.depth + 1, 6)}`, children: children() }];
      case "strong": return [{ tag: "strong", children: children() }];
      case "em": return [{ tag: "em", children: children() }];
      case "del": return [{ tag: "del", children: children() }];
      case "codespan": return [{ tag: "code", text: token.text }];
      case "code": return [{ tag: "pre", children: [{ tag: "code", text: token.text }] }];
      case "blockquote": return [{ tag: "blockquote", children: children() }];
      case "br": return [{ tag: "br" }];
      case "hr": return [{ tag: "hr" }];
      case "list": return [{ tag: token.ordered ? "ol" : "ul", children: token.items.map((item: Tokens.ListItem) => ({ tag: "li", children: convert(item.tokens) })) }];
      case "table": return [{ tag: "table", children: [
        { tag: "thead", children: [{ tag: "tr", children: token.header.map((cell: Tokens.TableCell) => ({ tag: "th", children: convert(cell.tokens) })) }] },
        { tag: "tbody", children: token.rows.map((row: Tokens.TableCell[]) => ({ tag: "tr", children: row.map((cell: Tokens.TableCell) => ({ tag: "td", children: convert(cell.tokens) })) })) },
      ] }];
      case "link": return [{ tag: /^https?:\/\//i.test(token.href) ? "a" : "span", href: /^https?:\/\//i.test(token.href) ? token.href : undefined, children: children() }];
      case "image": return [{ text: token.text }]; // No remote image requests from model output.
      case "text": return "tokens" in token && token.tokens ? children() : [{ text: token.text }];
      default: return [{ text: "text" in token ? String(token.text) : token.raw }];
    }
  });
  return convert(lexer(text));
}
