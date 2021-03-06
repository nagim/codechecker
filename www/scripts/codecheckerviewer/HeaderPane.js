// -------------------------------------------------------------------------
//                     The CodeChecker Infrastructure
//   This file is distributed under the University of Illinois Open Source
//   License. See LICENSE.TXT for details.
// -------------------------------------------------------------------------

define([
  'dojo/_base/declare',
  'dojo/topic',
  'dojo/dom-construct',
  'dijit/layout/ContentPane',
  'dijit/popup',
  'dijit/TooltipDialog',
  'codechecker/hashHelper',
  'codechecker/HeaderMenu',
  'codechecker/util'],
function (declare, topic, dom, ContentPane, popup, TooltipDialog, hashHelper,
  HeaderMenu, util) {
  return declare(ContentPane, {
    postCreate : function () {
      this.inherited(arguments);

      var that = this;

      //--- Logo ---//

      var logoContainer = dom.create('div', {
        id : 'logo-container'
      }, this.domNode);

      var logo = dom.create('span', { id : 'logo' }, logoContainer);

      var logoText = dom.create('div', {
        id : 'logo-text',
        innerHTML : 'CodeChecker ' + CC_PROD_SERVICE.getPackageVersion()
      }, logoContainer);

      //--- Title ---//

      var title = dom.create('span', {
        id : 'logo-title',
        innerHTML : this.get('title')
      }, logoText);

      //--- Header menu ---//

      this._headerMenu = dom.create('div', {
        id : 'header-menu'
      }, this.domNode);

      //--- Logged in user ---//

      var user = CC_AUTH_SERVICE.getLoggedInUser();
      if (user.length > 0) {

        //--- Tooltip ---//

        var profileMenu = dom.create('div', { id : 'profile-menu'});
        var header = dom.create('div', {
          class : 'header',
          innerHTML : 'Logged in as '
        }, profileMenu);
        dom.create('span', { class : 'user-name', innerHTML : user}, header);

        //--- Permissions ---//

        var filter = new CC_AUTH_OBJECTS.PermissionFilter({ given : true });
        var permissions =
          CC_AUTH_SERVICE.getPermissionsForUser('SYSTEM', {}, filter).map(
          function (p) {
            return util.enumValueToKey(Permission, p);
          });

        if (permissions.length) {
          var permissionWrapper = dom.create('div', {
            class : 'permission-wrapper'
          }, profileMenu);
          var permissonTitle = dom.create('div', {
            class : 'permission-title',
            innerHTML : 'Permissions:'
          }, permissionWrapper);

          var list = dom.create('ul', {
            class : 'permissions'
          }, permissionWrapper);
          permissions.forEach(function (permission) {
            dom.create('li', {
              class : 'permission-item',
              innerHTML : permission
            }, list);
          });
        }

        var dialog = new TooltipDialog({
          content : profileMenu,
          onBlur : function () {
            popup.close(this);
          }
        });

        //--- Logged in user and avatar ---//

        var loggedIn = dom.create('span', {
          id : 'logged-in',
          onclick : function () {
            popup.open({
              popup : dialog,
              around : this
            });
            dialog.focus();
          }
        }, this._headerMenu);

        var avatarWrapper = dom.create('span', {
          class : 'avatar-wrapper'
        }, loggedIn);

        var avatar = util.createAvatar(user);
        dom.place(avatar, avatarWrapper);

        var userName = dom.create('span', {
          class : 'user-name',
          innerHTML : user
        }, loggedIn);
      }

      if (this.menuItems)
        this.menuItems.forEach(function (menuItem) {
          dom.place(menuItem, that._headerMenu);
        });

      //--- Header menu buttons ---//

      var headerMenuButton = new HeaderMenu({
        class : 'main-menu-button',
        iconClass : 'dijitIconFunction',
      });
      dom.place(headerMenuButton.domNode, this._headerMenu);

      this.subscribeTopics();
    },

    subscribeTopics : function () {
      var that = this;

      topic.subscribe('tab/userguide', function () {
        if (!that.userguide) {
          that.userguide = new ContentPane({
            title : 'User guide',
            closable : true,
            href  : 'userguide/doc/html/md_userguide.html',
            onClose : function () {
              delete that.userguide;
              return true;
            },
            onShow : function () {
              hashHelper.resetStateValues({ 'tab' : 'userguide' });
            }
          });
          that.mainTab.addChild(that.userguide);
        }

        hashHelper.resetStateValues({ 'tab' : 'userguide' });
        that.mainTab.selectChild(that.userguide);
      });
    }
  });
});
