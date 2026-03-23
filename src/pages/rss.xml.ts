import rss from '@astrojs/rss';
import { getCollection } from 'astro:content';
import type { APIContext } from 'astro';

export async function GET(context: APIContext) {
  const articles = await getCollection('news');
  const sorted = articles.sort(
    (a, b) => b.data.pubDate.getTime() - a.data.pubDate.getTime(),
  );

  return rss({
    title: 'pulse360 — The Global Pulse',
    description:
      'AI-synthesized global news from 195+ sources, refreshed every 4 hours.',
    site: context.site!,
    items: sorted.slice(0, 50).map((article) => ({
      title: article.data.title,
      pubDate: article.data.pubDate,
      description: article.data.description,
      link: `/news/${article.id}`,
      categories: [article.data.category],
    })),
  });
}
