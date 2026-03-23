import { getCollection } from 'astro:content';
import type { APIContext } from 'astro';

export async function GET(_context: APIContext) {
  const articles = await getCollection('news');
  const index = articles
    .sort((a, b) => b.data.pubDate.getTime() - a.data.pubDate.getTime())
    .map((a) => ({
      slug: a.id,
      title: a.data.title,
      description: a.data.description,
      category: a.data.category,
      pubDate: a.data.pubDate.toISOString(),
      tags: a.data.tags ?? [],
      heroImage: a.data.heroImage ?? '',
    }));

  return new Response(JSON.stringify(index), {
    headers: { 'Content-Type': 'application/json' },
  });
}
