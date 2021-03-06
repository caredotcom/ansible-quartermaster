#!/usr/bin/env python
# coding: utf-8

""" ansible-quartermaster

 This is an Ansible dynamic inventory module written by Care.com, which gets
 information from two kinds of sources: YAML static inventory files, and
 dynamic inventory sources. It returns a unified output that Ansible can
 consume. See http://docs.ansible.com/developing_inventory.html for specs
 and details.

 As a module, this allows for easily accessing the Ansible inventory objects
 from any other python program.  See the 'aq' program for an example of
 using this module.

 :copyright: (c) 2016, Care.com
 :license: MIT License, see LICENSE for more details

"""

import copy
import glob
import json
import os
import subprocess
import yaml
import collections


def _recursive_update(d, u):
  """ Recursively update a dict

  Perform a recursive version of the dict 'update' function
  Function stolen shamelessly from http://stackoverflow.com/questions/3232943
  """
  for k, v in u.iteritems():
      if isinstance(v, collections.Mapping):
          r = _recursive_update(d.get(k, {}), v)
          d[k] = r
      else:
          d[k] = u[k]
  return d

def _parse_yamlfiles (yamlfiles):
  """ Parse a list of yaml files

  Parse the YAML files and return info about their contents. Takes a list
  of files as arguments; returns sourcedict and a dict of groups and
  includes to process later.
  """
  later = {}
  sourcedict = {}
  sourcedict['yaml'] = {}
  sourcedict['yaml']['_meta'] = {}
  sourcedict['yaml']['_meta']['hostvars'] = {}
  hostdict = sourcedict['yaml']['_meta']['hostvars']

  for yamlfile in yamlfiles:

    # Stuff the YAML from each file into yamldict for processing.

    with open(yamlfile, 'r') as yaml_fh:
      yamldict = yaml.load(yaml_fh)

      # Go through each key from the YAML file, and figure out whether
      # it's a dict of hosts, includes, metagroups, or groups.

      for yamlkey in yamldict.keys():

        # Process hosts into hostdict. See also the comment starting with
        # "Parse all the yaml files", before the call to _parse_yamlfiles,
        # for info about variable precedence and overriding and merging.

        if yamlkey == "hosts":
          for hostname in yamldict['hosts']:
            if hostname not in hostdict:
              hostdict[hostname] = yamldict[yamlkey][hostname]
            else:
              hostdict[hostname] = _recursive_update(hostdict[hostname], yamldict[yamlkey][hostname])

        # Set aside includes to process later.

        elif yamlkey == 'includes':
          if yamlkey not in later:
            later[yamlkey] = yamldict[yamlkey]
          else:
            later[yamlkey].update(yamldict[yamlkey])

        # Set aside metagroups to process later.

        elif yamlkey == 'metagroups':
          if yamlkey not in later:
            later[yamlkey] = yamldict[yamlkey]
          else:
            later[yamlkey].update(yamldict[yamlkey])

        # Set aside groups to process later. See also the comment starting with
        # "Parse all the yaml files", before the call to _parse_yamlfiles,
        # for info about variable precedence and overriding and merging.

        elif yamlkey == 'groups':
          for groupkey in yamldict[yamlkey].keys():
            if yamlkey not in later:
              later[yamlkey] = yamldict[yamlkey]
            else:
              later[yamlkey].setdefault(groupkey,{})
              later[yamlkey][groupkey] = _recursive_update(later[yamlkey][groupkey], yamldict[yamlkey][groupkey])

        # Anything other than 'hosts', 'includes', 'metagroups', or
        # 'groups'? Bail now.

        else:
          raise KeyError('Unknown key %s in %s' % ( yamlkey, yamlfile))

  return(sourcedict, later)


def _create_groupdict(sourcedict):
  ''' Create a unified group dict

  Create a unified groupdict based on the various sources in sourcedict,
  and return it.
  '''
  groupdict = {}
  groupdict['_meta'] = {}
  groupdict['_meta']['hostvars'] = {}

  # Point the hostdict name at the hostvars dict in the special "_meta"
  # group, since it's effectively a dict of hosts.

  hostdict = groupdict['_meta']['hostvars']

  # Go through each of the sources.

  for source in sourcedict.keys():

    # In each source, go through its groups, merging them into groupdict.

    for group in sourcedict[source].keys():

      # If this is the special _meta group, just merge all the hostvars
      # from each host in this source into the hostvars for each host in
      # hostdict.

      if group == "_meta":
        for host in sourcedict[source][group]['hostvars']:
          if host in sourcedict[source][group]['hostvars']:
            hostdict.setdefault(host,{})
            hostdict[host].update(sourcedict[source][group]['hostvars'][host])

      # Otherwise, this is an actual group that the source provided, so
      # initialize the group in groupdict if necessary, and add the hosts
      # from the source to the set of hosts in this group.

      else:
        groupdict.setdefault(group,{})
        groupdict[group].setdefault('hosts',set())
        groupdict[group]['hosts'].update(sourcedict[source][group])

    # Once we're done with that, groupdict has all the groups that were
    # already defined by the various sources, and hostdict has all the
    # hostvars that were defined by the various sources. Go through
    # hostdict and add hosts to groups based on their variables.

    for hostname in hostdict.keys():

      for var in hostdict[hostname].keys():

        # A host with a 'systype' or 'stack' variable gets added to the
        # group corresponding to the value of that variable.

        if var in ['systype', 'stack']:
          group = hostdict[hostname][var]
          groupdict.setdefault(group,{})
          groupdict[group].setdefault('hosts',set())
          groupdict[group]['hosts'].add(hostname)

        # A host with an 'extra_groups' variable, whose value should be a
        # list of groups, gets added to all the groups in the list.

        if var == 'extra_groups':
          for group in hostdict[hostname][var]:
            groupdict.setdefault(group,{})
            groupdict[group].setdefault('hosts',set())
            groupdict[group]['hosts'].add(hostname)

        # A host with a 'rax_metadata' variable needs some further
        # processing. The value of the 'rax_metadata' key is a dict, whose
        # keys are regular host variables. For those variables, the values
        # of the ones that start with the prefix "czgroup_" are groups
        # that we want to add this host to (in groupdict); and, we want to
        # set a corresponding variable in hostdict, minus the prefix.

        if var == "rax_metadata":
          for raxvar in hostdict[hostname][var].keys():
            if raxvar.startswith("czgroup_"):
              group = hostdict[hostname][var][raxvar]
              groupdict.setdefault(group,{})
              groupdict[group].setdefault('hosts',set())
              groupdict[group]['hosts'].add(hostname)
              hostdict[hostname][raxvar.replace("czgroup_","",1)] = group

        # If the var starts with the prefix "ec2_tag_czgroup_", its value
        # is a group that we want to add this host to (in groupdict); and,
        # we want to set a corresponding variable in hostdict, minus the
        # prefix. If the var starts with any other prefix 'ec2_tag_', its
        # value is a var that we want to add to this host, minus the prefix.

        if var.startswith("ec2_tag_czgroup_"):
          group = hostdict[hostname][var]
          groupdict.setdefault(group,{})
          groupdict[group].setdefault('hosts',set())
          groupdict[group]['hosts'].add(hostname)
          hostdict[hostname][var.replace("ec2_tag_czgroup_","",1)] = group
        elif var.startswith("ec2_tag_"):
          thisvar = var.replace("ec2_tag_","",1)
          # AWS requires capitalized 'Name' to show up in the console so we
          # use that as a tag, but other places we use this var it is called
          # name (lowercase) so we make that conversion here.
          if thisvar == 'Name':
            thisvar = 'name'
          hostdict[hostname][thisvar] = hostdict[hostname][var]

  return(groupdict)


def _add_hosts_to_extra_groups(groupdict, latergroupdict):
  ''' Add extra groups as necessary

  Go through the groups in latergroupdict, looking for groups with an
  'extra_groups' variable. For each of those, go through the hosts in that
  group, and add them to all of the extra groups if they aren't already in
  there.
  '''
  for group in latergroupdict.keys():
    if 'extra_groups' in latergroupdict[group].keys():
      for extragroup in latergroupdict[group]['extra_groups']:
        groupdict.setdefault(group,{})
        groupdict[group].setdefault('hosts',set())
        groupdict.setdefault(extragroup,{})
        groupdict[extragroup].setdefault('hosts',set())
        groupdict[extragroup]['hosts'].update(groupdict[group]['hosts'])


def _add_hosts_to_metagroups(groupdict, metagroupspecs):
  ''' Add metagroups

  Go through the metagroup specifications, creating or updating the
  compound groups defined there.
  '''
  hostdict = groupdict['_meta']['hostvars']

  # Go through the hosts, and add them to any metagroups they should be in.

  for hostname in hostdict.keys():

    # Go through the metagroup specifications, and add this host to any
    # that it should be in.

    for metagroupspec in metagroupspecs:

      # Reset the group name to the empty string, and assume that we
      # haven't failed until we do.

      metagroup = ''
      failed = False

      # Go through the components in the metagroup spec, each of which is
      # a variable name or a group name.

      for (index, component) in enumerate(metagroupspec):

        # If the component is a variable name, and this host has that
        # variable set, add the value of the variable to the name of the
        # metagroup; otherwise (the host doesn't have this variable set),
        # continue to the next metagroupspec (because this host isn't in
        # this metagroup).

        if 'variable' in component:
          varname = component['variable']
          if varname in hostdict[hostname]:
            metagroup += hostdict[hostname][varname]
          else:
            failed = True
            break

        # Otherwise, if the component is a group, and this group exists in
        # groupdict at all and host is in that group, add the name of the
        # group to the name of the metagroup; otherwise (the host isn't in
        # this group, or the group doesn't exist at all in groupdict),
        # continue to the next metagroupspec (because this host isn't in
        # this metagroup).

        if 'group' in component:
          group = component['group']
          if group in groupdict and hostname in groupdict[group]['hosts']:
            metagroup += group
          else:
            failed = True
            break

        # If this isn't the last component, add a dash to the name of the
        # metagroup.

        if index < len(metagroupspec)-1:
          metagroup += '-'

      # Now that we're done checking all the components, if we haven't
      # failed, actually add the host to this group.

      if failed == False:
        groupdict.setdefault(metagroup,{})
        groupdict[metagroup].setdefault('hosts',set())
        groupdict[metagroup]['hosts'].add(hostname)

def _handle_extra_groups_and_metagroups(groupdict, later):
  ''' Look for groups & metagroups declarations and process them

  Check to see if a 'groups' or 'metagroups' key exists and 
  expand them.  Iterate through all keys until all 'groups' and
  'metagroups' keys have been processed.
  '''

  groupdict_orig = {}
  while groupdict != groupdict_orig:

    # Make a copy of groupdict, so we can check if anything changes.

    groupdict_orig = copy.deepcopy(groupdict)

    # Add hosts to extra_groups and metagroups.

    if 'groups' in later.keys():
      _add_hosts_to_extra_groups(groupdict, later['groups'])

    if 'metagroups' in later.keys():
      _add_hosts_to_metagroups(groupdict, later['metagroups'])

def fetch_inventory(yamldir, ignore_not_ready_hosts=False):
  ''' Main function to read and process a directory full of YAML files

  Given a directory, load all the .yaml files that are there and create
  an Ansible inventory dict.  

  ignore_not_ready_hosts will cause any hosts with a 'rediness' key
  to be discarded if the value of the key is not 'ready'
  '''

  yamlfiles = glob.glob("{0}/*.yaml".format(yamldir))

  # Add in the hardcoded private directory, which lives in the same place on
  # each of our ansible servers, and can also contain an inventory.d directory.

  private_yamldir = "/ansible/private/inventory.d"
  private_yamlfiles = glob.glob("{0}/*.yaml".format(private_yamldir))

  # Parse all the yaml files we found above. Put the information about the
  # hosts into sourcedict (sourcedict['yaml'] actually, but we're creating
  # sourcedict at this point), and save the groups and includes information
  # for later.
  #
  # Note that variables from files in private_yamldir will override
  # previously specified vars from files in yamldir. Within a group or host,
  # the variables will be merged, so if you set foo in yamldir and bar in
  # private_yamldir, you'll get both; BUT, if the same variable is defined
  # for a given group or host, the one from private_yamldir will take
  # precedence. This is true whether the variable is a simple string or a
  # complex data structure! So if you try to set foo['bar'] for a host in
  # yamldir, and foo['baz'] for a host in private_yamldir, you will *only*
  # get foo['baz'].

  [sourcedict, later] = _parse_yamlfiles(yamlfiles + private_yamlfiles)

  # If there was an 'includes' section with other sources, run each of the
  # dynamic inventory scripts defined there, and stick the results into a
  # dict for that source.

  if 'includes' in later.keys():
    for source in sorted(later['includes'].keys()):
      p = subprocess.Popen([os.path.expanduser(later['includes'][source]), "--list"],
                           stdout=subprocess.PIPE)
      sourcedict[source] = json.loads(p.communicate()[0])

  # At this point, sourcedict has a key for each source, whose value is dict
  # of information about the groups of hosts from that source, including a
  # _meta group with host information. Now we need to create a consolidated
  # dict of all the groups based on all the sources, potentially adding some
  # hosts to additional groups as well.

  groupdict = _create_groupdict(sourcedict)

  # Now that we've got everything from all sources in one groupdict, go back
  # and handle the the groups and metagroups sections of the 'later' dict.

  _handle_extra_groups_and_metagroups(groupdict, later)

  # At this point, everyone who was in any groups should be in those groups,
  # so now find anyone who still *isn't* in any groups, and put them into
  # 'ungrouped'. First, initialize the group.

  groupdict.setdefault('ungrouped',{})
  groupdict['ungrouped'].setdefault('hosts',set())

  # Then, point the hostdict name at the hostvars dict in the special
  # "_meta" group, since it's effectively a dict of hosts, and go through
  # those hosts who aren't in any group.

  hostdict = groupdict['_meta']['hostvars']

  for hostname in hostdict.keys():

    # Assume that this host is ungrouped until proven otherwise.

    ungrouped = True

    # Go through all the groups; if this host is in any of them, remember
    # that it's not ungrouped, and bail out of the loop.

    for group in groupdict.values():
      if 'hosts' in group.keys() and hostname in group['hosts']:
        ungrouped = False
        continue

    # If we're ungrouped, add the host to the ungrouped group.

    if ungrouped:
      groupdict['ungrouped']['hosts'].add(hostname)

  # Now that groupdict has all the groups, go back and add to groupdict any
  # group variables that were defined in later['groups'].

  if "groups" in later.keys():
    for group in later['groups'].keys():
      groupdict.setdefault(group,{})
      groupdict[group]['vars'] = later['groups'][group]

  # If we're ignoring not-ready hosts, go through hostdict looking for
  # not-ready hosts to drop.

  if ignore_not_ready_hosts:

    for hostname in hostdict.keys():
      if 'readiness' in hostdict[hostname] and \
             hostdict[hostname]['readiness'] != "ready":

        # Pop the host off of hostdict.

        hostdict.pop(hostname)

        # Go through the list of groups looking for this hostname, and
        # remove it from any groups it's in; if that leaves the group with
        # no hosts in it, remove the group too.

        for (groupname, group) in groupdict.items():
          if 'hosts' in group and hostname in group['hosts']:
            group['hosts'].remove(hostname)
            if len(group['hosts']) == 0:
              groupdict.pop(groupname)

  # Finally, turn all the groups' host lists, which we've been representing
  # as sets, into actual lists, so we can JSON serialize them.

  for group in groupdict.values():
    if 'hosts' in group.keys() and isinstance(group['hosts'], set):
      group['hosts'] = sorted(list(group['hosts']))

  # Done processing! Return the groupdict object
  return groupdict

def groups(groupdict):
  ''' Return just the groups

  Strip out all the vars from the groupdict and return whats left
  '''
  mygroupdict = groupdict
  mygroupdict.pop('_meta', None)
  for group in mygroupdict.values():
    group.pop('vars', None)

  return mygroupdict

def group(groupdict, group):
  ''' Return just the contents of the specified group '''

  mygroupdict = groupdict
  mygroupdict.pop('_meta', None)

  if group in mygroupdict:
    return mygroupdict[group]['hosts']
  else:
    return [] 

def host(groupdict, host):
  ''' Return just the vars for the specified host '''

  hostdict = groupdict['_meta']['hostvars']

  if host in hostdict:
    return hostdict[host]
  else:
    return {}
