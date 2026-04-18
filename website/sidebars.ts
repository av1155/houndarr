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
      label: 'Install',
      collapsed: false,
      items: [
        'guides/installation/docker-compose',
        'guides/installation/docker',
        'guides/installation/unraid',
        'guides/installation/kubernetes',
        'guides/installation/helm',
        'guides/installation/from-source',
        'guides/first-run-setup',
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
        'guides/reverse-proxy',
        'guides/sso-proxy-auth',
      ],
    },
    {
      type: 'category',
      label: 'Reference',
      collapsed: false,
      items: [
        'reference/environment-variables',
        'reference/instance-settings',
        'reference/search-commands',
        'reference/skip-reasons',
        'reference/compatibility',
      ],
    },
    {
      type: 'category',
      label: 'Concepts',
      collapsed: false,
      items: [
        'concepts/how-scheduling-works',
        'concepts/search-order',
      ],
    },
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
    'faq',
  ],
};

export default sidebars;
