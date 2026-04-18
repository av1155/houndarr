import {themes as prismThemes} from 'prism-react-renderer';
import type {Config} from '@docusaurus/types';
import type * as Preset from '@docusaurus/preset-classic';

const config: Config = {
  title: 'Houndarr',
  tagline: 'Polite, automated media searching for your *arr stack',
  favicon: 'img/houndarr-logo-dark.png',

  future: {
    v4: true,
    faster: true,
  },

  url: 'https://av1155.github.io',
  baseUrl: '/houndarr/',

  organizationName: 'av1155',
  projectName: 'houndarr',

  trailingSlash: false,

  onBrokenLinks: 'throw',

  markdown: {
    hooks: {
      onBrokenMarkdownLinks: 'throw',
    },
  },

  i18n: {
    defaultLocale: 'en',
    locales: ['en'],
  },

  presets: [
    [
      'classic',
      {
        docs: {
          sidebarPath: './sidebars.ts',
          editUrl: 'https://github.com/av1155/houndarr/edit/main/website/',
        },
        blog: false,
        theme: {
          customCss: './src/css/custom.css',
        },
      } satisfies Preset.Options,
    ],
  ],

  plugins: [
    [
      '@docusaurus/plugin-client-redirects',
      {
        redirects: [
          {
            from: '/docs/configuration/instance-settings',
            to: '/docs/reference/instance-settings',
          },
          {
            from: '/docs/concepts/faq',
            to: '/docs/faq',
          },
          {
            from: '/docs/concepts/troubleshooting',
            to: '/docs/guides/troubleshoot-connection',
          },
          {
            from: '/docs/concepts/test-coverage',
            to: '/docs/security/audit',
          },
          {
            from: '/docs/security/trust-and-security',
            to: '/docs/security/overview',
          },
        ],
      },
    ],
  ],

  themeConfig: {
    image: 'img/social_preview.jpg',
    colorMode: {
      defaultMode: 'dark',
      respectPrefersColorScheme: true,
    },
    navbar: {
      title: 'Houndarr',
      logo: {
        alt: 'Houndarr logo',
        src: 'img/houndarr-logo-dark.png',
        srcDark: 'img/houndarr-logo-dark.png',
      },
      items: [
        {
          type: 'docSidebar',
          sidebarId: 'docsSidebar',
          position: 'left',
          label: 'Docs',
        },
        {
          href: 'https://github.com/av1155/houndarr',
          label: 'GitHub',
          position: 'right',
        },
        {
          href: 'https://ko-fi.com/av1155',
          label: 'Ko-fi',
          position: 'right',
        },
      ],
    },
    footer: {
      style: 'dark',
      links: [
        {
          title: 'Documentation',
          items: [
            {
              label: 'Quick Start',
              to: '/docs/getting-started/quick-start',
            },
            {
              label: 'Installation',
              to: '/docs/getting-started/installation',
            },
            {
              label: 'Instance Settings',
              to: '/docs/reference/instance-settings',
            },
            {
              label: 'How Houndarr Works',
              to: '/docs/concepts/how-houndarr-works',
            },
            {
              label: 'Audit',
              to: '/docs/security/audit',
            },
          ],
        },
        {
          title: 'Security',
          items: [
            {
              label: 'Security Overview',
              to: '/docs/security/overview',
            },
            {
              label: 'Report a Vulnerability',
              href: 'https://github.com/av1155/houndarr/security/advisories/new',
            },
          ],
        },
        {
          title: 'Links',
          items: [
            {
              label: 'GitHub',
              href: 'https://github.com/av1155/houndarr',
            },
            {
              label: 'Docker (GHCR)',
              href: 'https://github.com/av1155/houndarr/pkgs/container/houndarr',
            },
            {
              label: 'Ko-fi',
              href: 'https://ko-fi.com/av1155',
            },
          ],
        },
      ],
      copyright: `Copyright \u00A9 ${new Date().getFullYear()} Houndarr. Built with Docusaurus.`,
    },
    prism: {
      theme: prismThemes.github,
      darkTheme: prismThemes.dracula,
      additionalLanguages: ['bash', 'yaml', 'docker'],
    },
  } satisfies Preset.ThemeConfig,
};

export default config;
