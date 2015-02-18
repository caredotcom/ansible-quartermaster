# ansible-quartermaster

The Production Operations team at Care.com uses Ansible to manage our
production web infrastructure. We wrote a dynamic inventory script called
Ansible Quartermaster (or AQ), to tie together multiple other dynamic
inventory scripts (for example, of cloud systems at Rackspace, AWS, etc)
and a static inventory file (e.g. for dedicated hardware systems). This
gives us a single unified inventory that includes all of our
Ansible-managed systems, and thus allows us to use Ansible to manage them
all in a single coherent way, regardless of who provides the systems (or
how).
