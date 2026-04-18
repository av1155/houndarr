import type {SidebarsConfig} from '@docusaurus/plugin-content-docs';

const sidebars: SidebarsConfig = {
  docsSidebar: [
    {
      type: 'category',
      label: 'Tutorial',
      collapsed: false,
      items: [
        'tutorial/your-first-cycle',
      ],
    },
    {
      type: 'category',
      label: 'Getting Started',
      collapsed: false,
      items: [
        'getting-started/quick-start',
        'getting-started/installation',
        'guides/installation/unraid',
        'getting-started/kubernetes',
        'getting-started/helm',
        'getting-started/first-run-setup',
      ],
    },
    {
      type: 'category',
      label: 'Configuration',
      collapsed: false,
      items: [
        'configuration/environment-variables',
        'configuration/reverse-proxy',
      ],
    },
    {
      type: 'category',
      label: 'Reference',
      collapsed: false,
      items: [
        'reference/instance-settings',
        'reference/search-commands',
        'reference/skip-reasons',
      ],
    },
    {
      type: 'category',
      label: 'Concepts',
      collapsed: false,
      items: [
        'concepts/how-houndarr-works',
        'concepts/search-order',
      ],
    },
    'faq',
    {
      type: 'category',
      label: 'Security',
      collapsed: false,
      items: [
        'security/overview',
        'security/credential-handling',
        'security/threat-model',
        'security/audit',
      ],
    },
    {
      type: 'category',
      label: 'Guides',
      collapsed: false,
      items: [
        'guides/verify-its-working',
        'guides/troubleshoot-connection',
        'guides/increase-throughput',
        'guides/backup-and-restore',
      ],
    },
  ],
};

export default sidebars;
