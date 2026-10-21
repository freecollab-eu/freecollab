import { t, useI18n } from '../i18n';
import { useState, useEffect } from 'react';
import { api, Post, Comment } from '../lib/api';
import { getSocket } from '../lib/socket';
import { formatDistanceToNow } from 'date-fns';

interface FlowProps {
  flowId: string;
  currentUserId: string;
}

export function Flow({ flowId, currentUserId }: FlowProps) {
  useI18n();
  const [posts, setPosts] = useState<Post[]>([]);
  const [newPostContent, setNewPostContent] = useState('');
  const [loading, setLoading] = useState(true);
  const [expandedComments, setExpandedComments] = useState<Set<string>>(new Set());

  useEffect(() => {
    loadPosts();
    const socket = getSocket();

    socket.on('new_post', handleNewPost);
    socket.on('post_updated', handlePostUpdated);
    socket.on('post_deleted', handlePostDeleted);
    socket.on('post_liked', handlePostLiked);

    return () => {
      socket.off('new_post', handleNewPost);
      socket.off('post_updated', handlePostUpdated);
      socket.off('post_deleted', handlePostDeleted);
      socket.off('post_liked', handlePostLiked);
    };
  }, [flowId]);

  const loadPosts = async () => {
    try {
      const data = await api.getFlow(flowId);
      setPosts(data);
    } catch (err) {
      console.error('Failed to load posts:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleNewPost = (data: { flow_id: string; post: Post }) => {
    if (data.flow_id === flowId) {
      setPosts(prev => [data.post, ...prev]);
    }
  };

  const handlePostUpdated = (data: { post_id: string; content: string; updated_at: string }) => {
    setPosts(prev => prev.map(p => 
      p.id === data.post_id 
        ? { ...p, content: data.content, updated_at: data.updated_at }
        : p
    ));
  };

  const handlePostDeleted = (data: { post_id: string }) => {
    setPosts(prev => prev.filter(p => p.id !== data.post_id));
  };

  const handlePostLiked = (data: { post_id: string; like_count: number }) => {
    setPosts(prev => prev.map(p => 
      p.id === data.post_id ? { ...p, like_count: data.like_count } : p
    ));
  };

  const createPost = async () => {
    if (!newPostContent.trim()) return;
    try {
      await api.createPost(flowId, newPostContent);
      setNewPostContent('');
    } catch (err) {
      console.error('Failed to create post:', err);
    }
  };

  const toggleComments = (postId: string) => {
    setExpandedComments(prev => {
      const next = new Set(prev);
      if (next.has(postId)) {
        next.delete(postId);
      } else {
        next.add(postId);
      }
      return next;
    });
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500"></div>
      </div>
    );
  }

  return (
    <div className="max-w-2xl mx-auto p-4">
      {/* Create Post */}
      <div className="bg-white dark:bg-gray-800 rounded-lg shadow p-4 mb-6">
        <textarea
          value={newPostContent}
          onChange={(e) => setNewPostContent(e.target.value)}
          placeholder={t("What's on your mind?")}
          className="w-full p-3 border rounded-lg resize-none dark:bg-gray-700 dark:border-gray-600 dark:text-white"
          rows={3}
        />
        <div className="flex justify-end mt-2">
          <button
            onClick={createPost}
            disabled={!newPostContent.trim()}
            className="px-4 py-2 bg-blue-500 text-white rounded-lg hover:bg-blue-600 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {t("Post")}</button>
        </div>
      </div>

      {/* Posts List */}
      <div className="space-y-4">
        {posts.length === 0 ? (
          <div className="text-center text-gray-500 py-8">
            {t("No posts yet. Be the first to post!")}</div>
        ) : (
          posts.map(post => (
            <PostCard
              key={post.id}
              post={post}
              currentUserId={currentUserId}
              showComments={expandedComments.has(post.id)}
              onToggleComments={() => toggleComments(post.id)}
            />
          ))
        )}
      </div>
    </div>
  );
}

interface PostCardProps {
  post: Post;
  currentUserId: string;
  showComments: boolean;
  onToggleComments: () => void;
}

function PostCard({ post, currentUserId, showComments, onToggleComments }: PostCardProps) {
  useI18n();
  const [isEditing, setIsEditing] = useState(false);
  const [editContent, setEditContent] = useState(post.content);
  const [liked, setLiked] = useState(false);
  const [likeCount, setLikeCount] = useState(post.like_count);

  const isAuthor = post.author_id === currentUserId;

  const handleLike = async () => {
    try {
      if (liked) {
        const result = await api.unlikePost(post.id);
        setLikeCount(result.like_count);
        setLiked(false);
      } else {
        const result = await api.likePost(post.id);
        setLikeCount(result.like_count);
        setLiked(true);
      }
    } catch (err) {
      console.error('Failed to toggle like:', err);
    }
  };

  const handleEdit = async () => {
    try {
      await api.updatePost(post.id, editContent);
      setIsEditing(false);
    } catch (err) {
      console.error('Failed to update post:', err);
    }
  };

  const handleDelete = async () => {
    if (!confirm(t("Are you sure you want to delete this post?"))) return;
    try {
      await api.deletePost(post.id);
    } catch (err) {
      console.error('Failed to delete post:', err);
    }
  };

  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg shadow p-4">
      {/* Header */}
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center space-x-3">
          <div className="w-10 h-10 bg-blue-500 rounded-full flex items-center justify-center text-white font-semibold">
            {(post.author_name || 'U')[0].toUpperCase()}
          </div>
          <div>
            <p className="font-semibold dark:text-white">{post.author_name || t("Unknown User")}</p>
            <p className="text-sm text-gray-500">
              {formatDistanceToNow(new Date(post.created_at), { addSuffix: true })}
              {post.updated_at && ' (edited)'}
            </p>
          </div>
        </div>
        {isAuthor && (
          <div className="flex space-x-2">
            <button
              onClick={() => setIsEditing(!isEditing)}
              className="text-gray-500 hover:text-blue-500"
            >
              ✏️
            </button>
            <button
              onClick={handleDelete}
              className="text-gray-500 hover:text-red-500"
            >
              🗑️
            </button>
          </div>
        )}
      </div>

      {/* Content */}
      {isEditing ? (
        <div className="mb-3">
          <textarea
            value={editContent}
            onChange={(e) => setEditContent(e.target.value)}
            className="w-full p-2 border rounded dark:bg-gray-700 dark:border-gray-600 dark:text-white"
            rows={3}
          />
          <div className="flex justify-end space-x-2 mt-2">
            <button
              onClick={() => setIsEditing(false)}
              className="px-3 py-1 text-gray-600 dark:text-gray-300"
            >
              {t("Cancel")}</button>
            <button
              onClick={handleEdit}
              className="px-3 py-1 bg-blue-500 text-white rounded"
            >
              {t("Save")}</button>
          </div>
        </div>
      ) : (
        <p className="dark:text-white mb-3 whitespace-pre-wrap">{post.content}</p>
      )}

      {/* Actions */}
      <div className="flex items-center space-x-4 pt-3 border-t dark:border-gray-700">
        <button
          onClick={handleLike}
          className={`flex items-center space-x-1 ${liked ? 'text-red-500' : 'text-gray-500 hover:text-red-500'}`}
        >
          <span>{liked ? '❤️' : '🤍'}</span>
          <span>{likeCount}</span>
        </button>
        <button
          onClick={onToggleComments}
          className="flex items-center space-x-1 text-gray-500 hover:text-blue-500"
        >
          <span>💬</span>
          <span>{post.comment_count}</span>
        </button>
      </div>

      {/* Comments Section */}
      {showComments && (
        <CommentsSection
          entityType="post"
          entityId={post.id}
          currentUserId={currentUserId}
        />
      )}
    </div>
  );
}

interface CommentsSectionProps {
  entityType: string;
  entityId: string;
  currentUserId: string;
}

function CommentsSection({ entityType, entityId, currentUserId }: CommentsSectionProps) {
  useI18n();
  const [comments, setComments] = useState<Comment[]>([]);
  const [newComment, setNewComment] = useState('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadComments();
    const socket = getSocket();

    socket.on('new_comment', handleNewComment);
    socket.on('comment_updated', handleCommentUpdated);
    socket.on('comment_deleted', handleCommentDeleted);

    return () => {
      socket.off('new_comment', handleNewComment);
      socket.off('comment_updated', handleCommentUpdated);
      socket.off('comment_deleted', handleCommentDeleted);
    };
  }, [entityType, entityId]);

  const loadComments = async () => {
    try {
      const data = await api.getComments(entityType, entityId);
      setComments(data);
    } catch (err) {
      console.error('Failed to load comments:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleNewComment = (data: { entity_type: string; entity_id: string; comment: Comment }) => {
    if (data.entity_type === entityType && data.entity_id === entityId) {
      setComments(prev => [...prev, data.comment]);
    }
  };

  const handleCommentUpdated = (data: { comment_id: string; content: string }) => {
    setComments(prev => prev.map(c =>
      c.id === data.comment_id ? { ...c, content: data.content } : c
    ));
  };

  const handleCommentDeleted = (data: { comment_id: string }) => {
    setComments(prev => prev.filter(c => c.id !== data.comment_id));
  };

  const submitComment = async () => {
    if (!newComment.trim()) return;
    try {
      await api.createComment(entityType, entityId, newComment);
      setNewComment('');
    } catch (err) {
      console.error('Failed to create comment:', err);
    }
  };

  if (loading) {
    return <div className="mt-4 text-center text-gray-500">{t("Loading comments...")}</div>;
  }

  return (
    <div className="mt-4 pt-4 border-t dark:border-gray-700">
      {/* Comment Input */}
      <div className="flex space-x-2 mb-4">
        <input
          type="text"
          value={newComment}
          onChange={(e) => setNewComment(e.target.value)}
          placeholder={t("Write a comment...")}
          className="flex-1 px-3 py-2 border rounded-lg dark:bg-gray-700 dark:border-gray-600 dark:text-white"
          onKeyDown={(e) => e.key === 'Enter' && submitComment()}
        />
        <button
          onClick={submitComment}
          disabled={!newComment.trim()}
          className="px-4 py-2 bg-blue-500 text-white rounded-lg disabled:opacity-50"
        >
          {t("Send")}</button>
      </div>

      {/* Comments List */}
      <div className="space-y-3">
        {comments.map(comment => (
          <CommentItem
            key={comment.id}
            comment={comment}
            currentUserId={currentUserId}
          />
        ))}
      </div>
    </div>
  );
}

interface CommentItemProps {
  comment: Comment;
  currentUserId: string;
  isReply?: boolean;
}

function CommentItem({ comment, currentUserId, isReply = false }: CommentItemProps) {
  useI18n();
  const [showReplies, setShowReplies] = useState(false);
  const [replies, setReplies] = useState<Comment[]>([]);
  const [replyText, setReplyText] = useState('');
  const [isEditing, setIsEditing] = useState(false);
  const [editContent, setEditContent] = useState(comment.content);
  const [liked, setLiked] = useState(false);
  const [likeCount, setLikeCount] = useState(comment.like_count);

  const isAuthor = comment.author_id === currentUserId;

  const loadReplies = async () => {
    try {
      const data = await api.getCommentReplies(comment.id);
      setReplies(data);
      setShowReplies(true);
    } catch (err) {
      console.error('Failed to load replies:', err);
    }
  };

  const submitReply = async () => {
    if (!replyText.trim()) return;
    try {
      const newReply = await api.createComment(comment.entity_type, comment.entity_id, replyText, comment.id);
      setReplies(prev => [...prev, newReply]);
      setReplyText('');
    } catch (err) {
      console.error('Failed to submit reply:', err);
    }
  };

  const handleLike = async () => {
    try {
      if (liked) {
        const result = await api.unlikeComment(comment.id);
        setLikeCount(result.like_count);
        setLiked(false);
      } else {
        const result = await api.likeComment(comment.id);
        setLikeCount(result.like_count);
        setLiked(true);
      }
    } catch (err) {
      console.error('Failed to toggle like:', err);
    }
  };

  const handleEdit = async () => {
    try {
      await api.updateComment(comment.id, editContent);
      setIsEditing(false);
    } catch (err) {
      console.error('Failed to update comment:', err);
    }
  };

  const handleDelete = async () => {
    if (!confirm(t("Delete this comment?"))) return;
    try {
      await api.deleteComment(comment.id);
    } catch (err) {
      console.error('Failed to delete comment:', err);
    }
  };

  return (
    <div className={`${isReply ? 'ml-8' : ''}`}>
      <div className="flex space-x-2">
        <div className="w-8 h-8 bg-gray-400 rounded-full flex items-center justify-center text-white text-sm">
          {(comment.author_name || 'U')[0].toUpperCase()}
        </div>
        <div className="flex-1">
          <div className="bg-gray-100 dark:bg-gray-700 rounded-lg px-3 py-2">
            <p className="font-semibold text-sm dark:text-white">{comment.author_name || t("Unknown")}</p>
            {isEditing ? (
              <div>
                <input
                  type="text"
                  value={editContent}
                  onChange={(e) => setEditContent(e.target.value)}
                  className="w-full px-2 py-1 border rounded dark:bg-gray-600 dark:text-white"
                />
                <div className="flex space-x-2 mt-1">
                  <button onClick={handleEdit} className="text-xs text-blue-500">{t("Save")}</button>
                  <button onClick={() => setIsEditing(false)} className="text-xs text-gray-500">{t("Cancel")}</button>
                </div>
              </div>
            ) : (
              <p className="text-sm dark:text-gray-200">{comment.content}</p>
            )}
          </div>
          
          {/* Comment Actions */}
          <div className="flex items-center space-x-3 mt-1 text-xs text-gray-500">
            <button onClick={handleLike} className={liked ? 'text-red-500' : 'hover:text-red-500'}>
              {liked ? '❤️' : t("Like")} {likeCount > 0 && likeCount}
            </button>
            {!isReply && (
              <button onClick={loadReplies} className="hover:text-blue-500">
                {t("Reply ")}{comment.reply_count > 0 && `(${comment.reply_count})`}
              </button>
            )}
            {isAuthor && (
              <>
                <button onClick={() => setIsEditing(true)} className="hover:text-blue-500">{t("Edit")}</button>
                <button onClick={handleDelete} className="hover:text-red-500">{t("Delete")}</button>
              </>
            )}
            <span>{formatDistanceToNow(new Date(comment.created_at), { addSuffix: true })}</span>
          </div>

          {/* Replies */}
          {showReplies && (
            <div className="mt-2 space-y-2">
              {replies.map(reply => (
                <CommentItem
                  key={reply.id}
                  comment={reply}
                  currentUserId={currentUserId}
                  isReply
                />
              ))}
              {/* Reply Input */}
              <div className="flex space-x-2 mt-2">
                <input
                  type="text"
                  value={replyText}
                  onChange={(e) => setReplyText(e.target.value)}
                  placeholder={t("Write a reply...")}
                  className="flex-1 px-2 py-1 text-sm border rounded dark:bg-gray-700 dark:border-gray-600 dark:text-white"
                  onKeyDown={(e) => e.key === 'Enter' && submitReply()}
                />
                <button
                  onClick={submitReply}
                  disabled={!replyText.trim()}
                  className="px-2 py-1 text-sm bg-blue-500 text-white rounded disabled:opacity-50"
                >
                  {t("Reply")}</button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default Flow;
