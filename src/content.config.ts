import { defineCollection, z } from 'astro:content';
import { glob } from 'astro/loaders';

const news = defineCollection({
  loader: glob({ pattern: '**/*.md', base: './src/content/news' }),
  schema: z.object({
    title: z.string(),
    pubDate: z.coerce.date(),
    description: z.string().max(200),
    country: z.string().optional().default(''),
    countryCode: z.string().optional().default(''),
    category: z.enum(['Politics', 'Economy', 'Sports', 'Showbiz', 'Tech']),
    sourceUrl: z.string().url(),
    heroImage: z.string().optional(),
    sentiment: z.enum(['Positive', 'Negative', 'Neutral']).default('Neutral'),
    tags: z.array(z.string()).default([]),
    source: z.string().optional().default(''),
    importance: z.number().optional().default(0),
    displayOrder: z.number().optional().default(999),
  }),
});

export const collections = { news };
