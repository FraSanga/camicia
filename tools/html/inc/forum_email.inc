<?php
// This file is part of BOINC.
// http://boinc.berkeley.edu
// Copyright (C) 2008 University of California
//
// BOINC is free software; you can redistribute it and/or modify it
// under the terms of the GNU Lesser General Public License
// as published by the Free Software Foundation,
// either version 3 of the License, or (at your option) any later version.
//
// BOINC is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
// See the GNU Lesser General Public License for more details.
//
// You should have received a copy of the GNU Lesser General Public License
// along with BOINC.  If not, see <http://www.gnu.org/licenses/>.

require_once("../inc/util.inc");
require_once("../inc/email.inc");
require_once("../project/project.inc");

// send an email to admins:
// - project forums: everyone in POST_REPORT_EMAILS
// - team message board: team founder and admins
//
function mail_report_list($forum, $subject, $body, $must_send=false) {
    $success = true;
    switch ($forum->parent_type) {
    case 0:
        if ($must_send && !defined("POST_REPORT_EMAILS")) {
            echo "This project has not yet defined an administrator to
                handle this kind of forum report.
                Please contact the project and tell them to add this information
                in their html/project/project.inc file
            ";
        }
        $emails = explode("|", POST_REPORT_EMAILS);
        foreach ($emails as $email) {
            $admin = new StdClass;
            $admin->email_addr = $email;
            $admin->name = "Admin";
            if (!send_email($admin, $subject, $body)) {
                $success = false;
            }
        }
        break;
    case 1:
        $team = BoincTeam::lookup_id($forum->category);
        $founder = BoincUser::lookup_id($team->userid);
        $success = send_email($founder, $subject, $body);
        $admins = BoincTeamAdmin::enum("teamid=$team->id");
        foreach ($admins as $admin) {
            $u = BoincUser::lookup_id($admin->userid);
            if (!$u) continue;
            $success &= send_email($u, $subject, $body);
        }
        break;
    }
    return $success;
}

//////////////////// post hidden/unhidden ///////////
//
function send_moderation_email($forum, $post, $thread, $explanation, $action) {
    $moderator = get_logged_in_user();
    $body = "";
    $user = BoincUser::lookup_id($post->user);

    $subject = PROJECT." moderation notice";

    $body = email_greeting($user)."
Your post [ID $post->id] in thread '$thread->title'
".secure_url_base()."forum_thread.php?id=$thread->id#$post->id
has been $action by moderator $moderator->name (ID $moderator->id).
$explanation

The content of your post:
$post->content";
    $body .= email_footer();

    $success = send_email($user, $subject, $body);
    pm_send_msg($user, $user, $subject, $body, false);

    $admin_body = "Because of moderation by $moderator->name (ID $moderator->id),
The following email was sent to $user->name (ID $user->id)
".secure_url_base()."forum_user_posts.php?userid=$user->id
------------------------------
Subject: $subject

$body
";
    $admin_body .= email_footer(false);
    $subject = PROJECT.": post $action in '$thread->title'";
    $success &= mail_report_list($forum, $subject, $admin_body);
    return $success;
}

//////////////////// thread hidden/unhidden ///////////
//
function send_thread_moderation_email(
    $forum, $thread, $message, $action_name, $explanation
) {
    $moderator = get_logged_in_user();
    $user = BoincUser::lookup_id($thread->owner);

    $subject = PROJECT." forum moderation notice";
    $body = email_greeting($user)."
Your thread '$thread->title'
".secure_url_base()."forum_thread.php?id=$thread->id
has been $action_name by moderator $moderator->name (ID $moderator->id).
$explanation";
    $body .= email_footer();

    $success = send_email($user, $subject, $body);
    pm_send_msg($user, $user, $subject, $body, false);

    $admin_subject = "THREAD $action_name REPORT: $thread->title";
    $admin_body = "Thread '$thread->title' (owner: $user->name, ID $user->id)
".secure_url_base()."forum_thread.php?id=$thread->id
has been $action_name by moderator $moderator->name (ID $moderator->id).
$explanation";
    $admin_body .= email_footer(false);
    $success &= mail_report_list($forum, $admin_subject, $admin_body);
    return $success;
}

// There's a new post in the thread, which the user is subscribed to.
// send them an email notifying them.
//
function send_thread_notification_email($thread, $user){
    $title = PROJECT . ": there is a new post in '". $thread->title ."'";
    $link = secure_url_base() . "forum_thread.php?id=" . $thread->id;
    $body = email_greeting($user)."
A " . PROJECT . " user has posted to the thread
\"" . $thread->title . "\".\n"
           ."To view the updated thread, visit:\n$link";
    $body .= email_footer();
    return send_email($user, $title, $body);
}

// There's a new thread in the forum, which the user is subscribed to.
// send them an email notifying them.
//
function send_forum_notification_email($forum, $user){
    $title = PROJECT . ": there is a new thread in '". $forum->title ."'";
    $link = secure_url_base() . "forum_forum.php?id=" . $forum->id;
    $body = email_greeting($user)."
A " . PROJECT . " user has added a thread to the forum
\"" . $forum->title . "\".\n"
           ."To view the updated forum, visit:\n$link";
    $body .= email_footer();
    return send_email($user, $title, $body);
}

//////////////////// a user clicks the red "x" to report a post ///////////
//
function send_report_post_email($user, $forum, $thread,  $post, $message) {
    $body = "";
    $owner = BoincUser::lookup_id($post->user);

    $subject = PROJECT." post in '$thread->title' reported as offensive";
    $body = PROJECT." notification:

A post in the ".PROJECT." forums was reported as offensive.
    Thread:         $thread->title
    Post:           $post->id by $owner->id ($owner->name)
    Reporting User: $user->id ($user->name)
    Link:           ".secure_url_base()."forum_thread.php?id=$thread->id#$post->id

Comments from reporting user:
$message

Contents of the post:
$post->content";
    $body .= email_footer(false);

    $success = mail_report_list($forum, $subject, $body, true);

    // if it's a forum board, send to project admins too
    //
    if ($forum->parent_type != 0) {
        $forum = new BoincForum;
        $forum->parent_type = 0;
        $success &= mail_report_list($forum, $subject, $body, true);
    }
    return $success;
}

//////////////////// a user has been banished ///////////
//
function send_banish_email($forum, $user, $duration, $reason) {
    $reason_text = "";
    if ($reason) {
        $reason_text = "\n\nThe moderator gave the following explanation about your suspension:\n$reason";
    }

    $subject = PROJECT." posting privileges suspended";
    $body = email_greeting($user)."
You will not be able to post to the ".PROJECT." message boards
until ".time_str($duration).",
because your postings have not followed our guidelines.
    ".$reason_text;
    $body .= email_footer();
    $success = send_email($user, $subject, $body);
    pm_send_msg($user, $user, $subject, $body, false);

    $admin_body = "$user->name (ID $user->id) will not be able to post to the ".PROJECT." message boards
until ".time_str($duration).".".$reason_text;
    $admin_body .= email_footer(false);
    $success &= mail_report_list($forum, "$user->name (ID $user->id) has been banished.", $admin_body);
    return $success;
}

//////////////////// a banishment vote has been started  ///////////
//
function send_banish_vote_email($user, $duration, $reason, $end_time) {
	$master_url = master_url();
    $now=time();
    $vote_url = $master_url."forum_banishment_vote.php";

    $reason_text = "";
    if ($reason) {
        $reason_text = "\n\nThe moderator gave the following reason for your pending suspension:\n$reason";
    }
    $vote_summary = "
A vote has been started to banish you
from the ".PROJECT." message boards until ".time_str($duration+$now).",
because your postings have not followed our guidelines.

This vote will last until ".time_str($end_time)." or until a majority
decision has been reached.  If the vote does not result in banishment, you will be
able to resume posting at that time.
    ".$reason_text;

    $subject = PROJECT." banishment vote underway";
    $body = email_greeting($user).$vote_summary;
    $body .= email_footer();
    $success = send_email($user, $subject, $body);
    pm_send_msg($user, $user, $subject, $body, false);

    $admin_body = "A vote has been started to banish ".$user->name." (ID $user->id)
from the ".PROJECT." message boards until ".time_str($duration+$now).",
because their postings have not followed our guidelines.

This vote will last until ".time_str($end_time)." or until a majority
decision has been reached.".$reason_text;
    $admin_body .= "\n\n<a href=".$vote_url."?action=yes&userid="
             .$user->id
           .">[vote to banish author]</a>\n\n"
           ."<a href=".$vote_url."?action=no&userid="
             .$user->id
           .">[vote not to banish author]</a>";
    $admin_body .= email_footer(false);

    $forum = new BoincForum;
    $forum->parent_type = 0;
    $success &= mail_report_list($forum, "A banishment vote for ".$user->name." has been started.", $admin_body);
    return $success;
}
?>
