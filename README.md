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

All the work of AQ is handled by the ansible_quartermaster.py module. We
have the need to reference the Ansible inventory in other Python scripts
that we write, so a module makes perfect sense.  To incorporate our 
Ansible inventory in another script is as simple as:

  ```python
  import ansible_quartermaster as aq
  inventory = aq.fetch_inventory('/ansible/conf/inventory.d')
  ```

# Installation

To install the ansible_quartermaster module simply run the following command:

  `sudo python setup.py install`


