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
  onBrokenAnchors: 'throw',

  markdown: {
    mermaid: true,
    hooks: {
      onBrokenMarkdownLinks: 'throw',
    },
  },

  themes: [
    '@docusaurus/theme-mermaid',
    [
      require.resolve('@easyops-cn/docusaurus-search-local'),
      {
        hashed: true,
        language: ['en'],
        docsRouteBasePath: '/docs',
        indexBlog: false,
        indexPages: true,
        highlightSearchTermsOnTargetPage: true,
      },
    ],
  ],

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
          showLastUpdateTime: true,
          showLastUpdateAuthor: true,
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
      '@docusaurus/plugin-ideal-image',
      {
        quality: 85,
        max: 1920,
        min: 640,
        steps: 3,
        disableInDev: false,
      },
    ],
    'docusaurus-plugin-image-zoom',
    [
      '@docusaurus/plugin-client-redirects',
      {
        redirects: [
          {
            from: '/docs/getting-started/quick-start',
            to: '/docs/guides/installation/docker-compose',
          },
          {
            from: '/docs/getting-started/installation',
            to: '/docs/guides/installation/docker',
          },
          {
            from: '/docs/getting-started/first-run-setup',
            to: '/docs/guides/first-run-setup',
          },
          {
            from: '/docs/getting-started/kubernetes',
            to: '/docs/guides/installation/kubernetes',
          },
          {
            from: '/docs/getting-started/helm',
            to: '/docs/guides/installation/helm',
          },
          {
            from: '/docs/configuration/environment-variables',
            to: '/docs/reference/environment-variables',
          },
          {
            from: '/docs/configuration/instance-settings',
            to: '/docs/reference/instance-settings',
          },
          {
            from: '/docs/configuration/reverse-proxy',
            to: '/docs/guides/reverse-proxy',
          },
          {
            from: '/docs/concepts/how-houndarr-works',
            to: '/docs/concepts/how-scheduling-works',
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
    image: 'img/houndarr-social-preview.png',
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
              to: '/docs/guides/installation/docker-compose',
            },
            {
              label: 'Installation',
              to: '/docs/guides/installation/docker',
            },
            {
              label: 'Instance Settings',
              to: '/docs/reference/instance-settings',
            },
            {
              label: 'How Houndarr Works',
              to: '/docs/concepts/how-scheduling-works',
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
    mermaid: {
      theme: {light: 'default', dark: 'dark'},
    },
    zoom: {
      selector: '.markdown :not(em) > img, .landing-zoomable img',
      background: {
        light: 'rgb(255, 255, 255)',
        dark: 'rgb(30, 41, 59)',
      },
      config: {
        margin: 24,
      },
    },
  } satisfies Preset.ThemeConfig,
};

export default config;
