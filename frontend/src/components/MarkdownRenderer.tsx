import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

interface MarkdownRendererProps {
  content: string;
  onSourceLink?: (href: string) => void;
}

export function MarkdownRenderer({ content, onSourceLink }: MarkdownRendererProps) {
  return (
    <div className="markdown-body">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a({ children, href, ...props }) {
            return (
              <a href={href} rel="noreferrer" target="_blank" {...props} onClick={event => {
                if (onSourceLink && href && /\/api\/research\/projects\/[^/]+\/sources\/[^/?]+/.test(href)) {
                  event.preventDefault(); onSourceLink(href);
                }
              }}>
                {children}
              </a>
            );
          }
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
