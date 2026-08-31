--
-- PostgreSQL database dump
--

-- Dumped from database version 14.18 (Homebrew)
-- Dumped by pg_dump version 14.18 (Homebrew)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: taskstatus; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.taskstatus AS ENUM (
    'PENDING',
    'RUNNING',
    'SUCCESS',
    'FAILED',
    'PAUSED'
);


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: app_configs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.app_configs (
    id integer NOT NULL,
    key character varying NOT NULL,
    value text,
    description character varying NOT NULL,
    category character varying NOT NULL,
    is_system boolean NOT NULL,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL,
    created_by character varying,
    updated_by character varying
);


--
-- Name: app_configs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.app_configs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: app_configs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.app_configs_id_seq OWNED BY public.app_configs.id;


--
-- Name: apscheduler_jobs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.apscheduler_jobs (
    id character varying(191) NOT NULL,
    next_run_time double precision,
    job_state bytea NOT NULL
);


--
-- Name: character_folders; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.character_folders (
    id integer NOT NULL,
    user_id character varying(50) NOT NULL,
    name character varying(100) NOT NULL,
    parent_id integer,
    display_order integer DEFAULT 0 NOT NULL,
    created_at timestamp without time zone DEFAULT (now() AT TIME ZONE 'UTC'::text) NOT NULL,
    updated_at timestamp without time zone DEFAULT (now() AT TIME ZONE 'UTC'::text) NOT NULL,
    is_deleted boolean DEFAULT false NOT NULL
);


--
-- Name: character_folders_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.character_folders_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: character_folders_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.character_folders_id_seq OWNED BY public.character_folders.id;


--
-- Name: characters; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.characters (
    id integer NOT NULL,
    user_id character varying(50) NOT NULL,
    storybook_id integer,
    name character varying(50) NOT NULL,
    photo_url character varying(255) NOT NULL,
    response_id character varying(100) NOT NULL,
    generation_id character varying(100) NOT NULL,
    description character varying NOT NULL,
    type character varying(20) NOT NULL,
    i18n_data character varying NOT NULL,
    display_order integer NOT NULL,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL,
    is_deleted boolean NOT NULL
);


--
-- Name: characters_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.characters_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: characters_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.characters_id_seq OWNED BY public.characters.id;


--
-- Name: characters_v2; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.characters_v2 (
    id integer NOT NULL,
    uuid character varying(36) NOT NULL,
    user_id character varying(50) NOT NULL,
    character_name character varying(100) NOT NULL,
    url character varying(500) NOT NULL,
    filename character varying(255) NOT NULL,
    folder_id integer,
    additional_data text DEFAULT '{}'::text NOT NULL,
    created_at timestamp without time zone DEFAULT (now() AT TIME ZONE 'UTC'::text) NOT NULL,
    updated_at timestamp without time zone DEFAULT (now() AT TIME ZONE 'UTC'::text) NOT NULL,
    is_deleted boolean DEFAULT false NOT NULL
);


--
-- Name: characters_v2_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.characters_v2_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: characters_v2_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.characters_v2_id_seq OWNED BY public.characters_v2.id;


--
-- Name: checkpoint_blobs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.checkpoint_blobs (
    thread_id text NOT NULL,
    checkpoint_ns text DEFAULT ''::text NOT NULL,
    channel text NOT NULL,
    version text NOT NULL,
    type text NOT NULL,
    blob bytea
);


--
-- Name: checkpoint_migrations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.checkpoint_migrations (
    v integer NOT NULL
);


--
-- Name: checkpoint_writes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.checkpoint_writes (
    thread_id text NOT NULL,
    checkpoint_ns text DEFAULT ''::text NOT NULL,
    checkpoint_id text NOT NULL,
    task_id text NOT NULL,
    idx integer NOT NULL,
    channel text NOT NULL,
    type text,
    blob bytea NOT NULL,
    task_path text DEFAULT ''::text NOT NULL
);


--
-- Name: checkpoints; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.checkpoints (
    thread_id text NOT NULL,
    checkpoint_ns text DEFAULT ''::text NOT NULL,
    checkpoint_id text NOT NULL,
    parent_checkpoint_id text,
    type text,
    checkpoint jsonb NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL
);


--
-- Name: conversation_messages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.conversation_messages (
    id integer NOT NULL,
    conversation_id integer NOT NULL,
    role character varying NOT NULL,
    content character varying NOT NULL,
    created_at timestamp without time zone NOT NULL,
    sequence integer NOT NULL,
    meta_data character varying,
    event_type character varying,
    event_data character varying,
    source character varying(20) DEFAULT 'main'::character varying NOT NULL,
    uuid character varying(255) DEFAULT (gen_random_uuid())::text NOT NULL,
    conversation_uuid character varying(255),
    run_id character varying(255),
    additional_data jsonb
);


--
-- Name: conversation_messages_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.conversation_messages_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: conversation_messages_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.conversation_messages_id_seq OWNED BY public.conversation_messages.id;


--
-- Name: conversation_runs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.conversation_runs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: conversation_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.conversation_runs (
    id integer DEFAULT nextval('public.conversation_runs_id_seq'::regclass) NOT NULL,
    uuid character varying(255) NOT NULL,
    conversation_id character varying(255) NOT NULL,
    conversation_uuid character varying(255),
    thread_id character varying(255) NOT NULL,
    run_id character varying(255) NOT NULL,
    user_id character varying(255) NOT NULL,
    agent_type character varying(20),
    user_option jsonb,
    user_input text,
    user_input_files jsonb,
    status character varying(50) DEFAULT 'running'::character varying,
    error_message text,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    completed_at timestamp without time zone,
    additional_data jsonb,
    billing_status character varying(32) DEFAULT NULL::character varying,
    cost_credits double precision,
    langsmith_cost numeric,
    cost numeric,
    cost_calculated boolean,
    credits_deducted boolean,
    credits_amount integer,
    run_type character varying(32) DEFAULT NULL::character varying,
    langsmith_status character varying(32) DEFAULT NULL::character varying
);


--
-- Name: conversation_states; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.conversation_states (
    id integer NOT NULL,
    conversation_id integer NOT NULL,
    thread_id character varying NOT NULL,
    state_data character varying NOT NULL,
    checkpoint_id character varying,
    created_at timestamp without time zone NOT NULL,
    step_name character varying,
    is_complete boolean NOT NULL
);


--
-- Name: conversation_states_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.conversation_states_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: conversation_states_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.conversation_states_id_seq OWNED BY public.conversation_states.id;


--
-- Name: conversations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.conversations (
    id integer NOT NULL,
    user_id character varying NOT NULL,
    thread_id character varying NOT NULL,
    title character varying,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL,
    last_active_at timestamp without time zone NOT NULL,
    is_active boolean NOT NULL,
    message_count integer NOT NULL,
    meta_data character varying,
    uuid character varying(255) DEFAULT (gen_random_uuid())::text NOT NULL,
    user_input text,
    user_input_files jsonb,
    user_option jsonb,
    agent_type character varying(20),
    additional_data jsonb
);


--
-- Name: conversations_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.conversations_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: conversations_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.conversations_id_seq OWNED BY public.conversations.id;


--
-- Name: creation_favorites; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.creation_favorites (
    id bigint NOT NULL,
    creation_id bigint NOT NULL,
    user_id character varying(50) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: creation_favorites_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.creation_favorites_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: creation_favorites_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.creation_favorites_id_seq OWNED BY public.creation_favorites.id;


--
-- Name: creation_likes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.creation_likes (
    id bigint NOT NULL,
    creation_id bigint NOT NULL,
    user_id character varying(50),
    fingerprint character varying(100),
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: creation_likes_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.creation_likes_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: creation_likes_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.creation_likes_id_seq OWNED BY public.creation_likes.id;


--
-- Name: creations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.creations (
    id bigint NOT NULL,
    uuid character varying(50) NOT NULL,
    user_id character varying(50) NOT NULL,
    title character varying(100) NOT NULL,
    description character varying(500),
    video_url text NOT NULL,
    cover_url text,
    tags jsonb DEFAULT '[]'::jsonb,
    source character varying(20) DEFAULT 'upload'::character varying NOT NULL,
    thread_id character varying(50),
    status character varying(20) DEFAULT 'processing'::character varying NOT NULL,
    view_count bigint DEFAULT 0 NOT NULL,
    like_count bigint DEFAULT 0 NOT NULL,
    comment_count bigint DEFAULT 0 NOT NULL,
    remix_count bigint DEFAULT 0 NOT NULL,
    is_deleted boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    published_at timestamp with time zone,
    favorite_count bigint DEFAULT 0 NOT NULL,
    share_count bigint DEFAULT 0 NOT NULL
);


--
-- Name: creations_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.creations_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: creations_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.creations_id_seq OWNED BY public.creations.id;


--
-- Name: credit_allocations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.credit_allocations (
    id integer NOT NULL,
    user_id character varying(50) NOT NULL,
    subscription_id integer NOT NULL,
    credits_allocated integer NOT NULL,
    remaining_credits integer NOT NULL,
    allocated_at timestamp(6) without time zone DEFAULT CURRENT_TIMESTAMP,
    expires_at timestamp(6) without time zone NOT NULL,
    is_expired boolean DEFAULT false,
    expired_at timestamp(6) without time zone,
    stripe_invoice_id character varying(255),
    billing_period_start timestamp(6) without time zone NOT NULL,
    billing_period_end timestamp(6) without time zone NOT NULL,
    created_at timestamp(6) without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp(6) without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: credit_allocations_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.credit_allocations_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    MAXVALUE 2147483647
    CACHE 1;


--
-- Name: credit_allocations_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.credit_allocations_id_seq OWNED BY public.credit_allocations.id;


--
-- Name: credit_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.credit_history (
    id integer NOT NULL,
    user_id character varying(50) NOT NULL,
    amount integer NOT NULL,
    balance integer NOT NULL,
    operation_type character varying(20) NOT NULL,
    description character varying(255) NOT NULL,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL,
    is_deleted boolean NOT NULL,
    credit_source character varying,
    allocation_id character varying,
    expires_at timestamp without time zone,
    reference_id character varying(64) DEFAULT NULL::character varying
);


--
-- Name: credit_history_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.credit_history_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: credit_history_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.credit_history_id_seq OWNED BY public.credit_history.id;


--
-- Name: curated_style_prompts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.curated_style_prompts (
    id integer NOT NULL,
    uuid character varying(36) NOT NULL,
    created_at timestamp with time zone DEFAULT (now() AT TIME ZONE 'utc'::text) NOT NULL,
    updated_at timestamp with time zone DEFAULT (now() AT TIME ZONE 'utc'::text) NOT NULL,
    category character varying(64) NOT NULL,
    name character varying(256) NOT NULL,
    name_zh character varying(256),
    thumbnail_url text,
    description_en text NOT NULL,
    description_zh text,
    sort_order integer DEFAULT 0,
    is_available boolean DEFAULT true NOT NULL
);


--
-- Name: curated_style_prompts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.curated_style_prompts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: curated_style_prompts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.curated_style_prompts_id_seq OWNED BY public.curated_style_prompts.id;


--
-- Name: email_verifications; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.email_verifications (
    id integer NOT NULL,
    email character varying(100) NOT NULL,
    otp_code_hash character varying(255) NOT NULL,
    expires_at timestamp(6) without time zone NOT NULL,
    is_used boolean DEFAULT false NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    created_at timestamp(6) without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    is_deleted boolean DEFAULT false NOT NULL
);


--
-- Name: email_verifications_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.email_verifications_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    MAXVALUE 2147483647
    CACHE 1;


--
-- Name: email_verifications_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.email_verifications_id_seq OWNED BY public.email_verifications.id;


--
-- Name: invite_code_quotas; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.invite_code_quotas (
    id integer NOT NULL,
    user_id character varying(50) NOT NULL,
    last_weekly_refresh_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: invite_code_quotas_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.invite_code_quotas_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: invite_code_quotas_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.invite_code_quotas_id_seq OWNED BY public.invite_code_quotas.id;


--
-- Name: invite_codes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.invite_codes (
    id integer NOT NULL,
    code character varying(50) NOT NULL,
    creator_id character varying(50) NOT NULL,
    used_by_id character varying(50),
    used_at timestamp without time zone,
    expires_at timestamp without time zone,
    is_used boolean NOT NULL,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL,
    is_deleted boolean NOT NULL,
    sent_at timestamp with time zone,
    status character varying(16) DEFAULT 'new'::character varying NOT NULL
);


--
-- Name: invite_codes_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.invite_codes_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: invite_codes_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.invite_codes_id_seq OWNED BY public.invite_codes.id;


--
-- Name: llm_api_logs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.llm_api_logs (
    id integer NOT NULL,
    provider_type character varying NOT NULL,
    model character varying NOT NULL,
    method character varying NOT NULL,
    current_method character varying NOT NULL,
    prompt text,
    params text,
    usage text,
    res_text text,
    res_image text,
    response_id character varying,
    generation_id character varying,
    user_id character varying,
    storybook_id integer,
    status character varying NOT NULL,
    error_message character varying,
    duration_ms integer,
    created_at timestamp without time zone NOT NULL,
    is_deleted boolean NOT NULL
);


--
-- Name: llm_api_logs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.llm_api_logs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: llm_api_logs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.llm_api_logs_id_seq OWNED BY public.llm_api_logs.id;


--
-- Name: paymentpackage; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.paymentpackage (
    id integer NOT NULL,
    name character varying(100) NOT NULL,
    description character varying(255),
    prices json DEFAULT '{}'::json NOT NULL,
    credits integer NOT NULL,
    is_active boolean NOT NULL,
    sort_order integer NOT NULL,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL,
    is_deleted boolean NOT NULL
);


--
-- Name: paymentpackage_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.paymentpackage_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: paymentpackage_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.paymentpackage_id_seq OWNED BY public.paymentpackage.id;


--
-- Name: paymentsettings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.paymentsettings (
    id integer NOT NULL,
    key character varying(100) NOT NULL,
    value character varying(500) NOT NULL,
    description character varying(255),
    is_active boolean NOT NULL,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL,
    is_deleted boolean NOT NULL
);


--
-- Name: paymentsettings_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.paymentsettings_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: paymentsettings_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.paymentsettings_id_seq OWNED BY public.paymentsettings.id;


--
-- Name: paymenttransaction; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.paymenttransaction (
    id integer NOT NULL,
    transaction_id character varying NOT NULL,
    user_id character varying NOT NULL,
    package_id integer,
    amount integer NOT NULL,
    amount_usd integer NOT NULL,
    currency character varying NOT NULL,
    credits integer NOT NULL,
    status character varying(50) NOT NULL,
    checkout_session_status character varying(16) DEFAULT 'open'::character varying,
    payment_method character varying(50),
    stripe_payment_intent_id character varying,
    stripe_checkout_session_id character varying,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL,
    completed_at timestamp without time zone,
    failed_at timestamp without time zone,
    failure_reason character varying,
    webhook_payload json,
    is_deleted boolean NOT NULL,
    deleted_at timestamp without time zone
);


--
-- Name: paymenttransaction_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.paymenttransaction_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: paymenttransaction_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.paymenttransaction_id_seq OWNED BY public.paymenttransaction.id;


--
-- Name: share_links; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.share_links (
    id bigint NOT NULL,
    created_by character varying(64) NOT NULL,
    target_type character varying(16) NOT NULL,
    target_key character varying(64) NOT NULL,
    token character varying(32) NOT NULL,
    target_snapshot text,
    expires_ms bigint DEFAULT 0 NOT NULL,
    is_revoked boolean DEFAULT false NOT NULL,
    view_count bigint DEFAULT 0 NOT NULL,
    play_count bigint DEFAULT 0 NOT NULL,
    unique_view_count bigint DEFAULT 0 NOT NULL,
    unique_play_count bigint DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: share_links_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.share_links_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: share_links_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.share_links_id_seq OWNED BY public.share_links.id;


--
-- Name: stories; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.stories (
    id integer NOT NULL,
    user_id character varying(50) NOT NULL,
    title character varying(100) NOT NULL,
    content character varying NOT NULL,
    type character varying(20) NOT NULL,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL,
    is_deleted boolean NOT NULL
);


--
-- Name: stories_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.stories_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: stories_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.stories_id_seq OWNED BY public.stories.id;


--
-- Name: storybook_styles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.storybook_styles (
    id integer NOT NULL,
    user_id character varying(50) NOT NULL,
    title character varying(50) NOT NULL,
    description character varying NOT NULL,
    image_url character varying(255) NOT NULL,
    type character varying(20) NOT NULL,
    i18n_data character varying NOT NULL,
    display_order integer NOT NULL,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL,
    is_deleted boolean NOT NULL
);


--
-- Name: storybook_styles_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.storybook_styles_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: storybook_styles_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.storybook_styles_id_seq OWNED BY public.storybook_styles.id;


--
-- Name: storybooks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.storybooks (
    id integer NOT NULL,
    user_id character varying(50),
    story_id integer,
    character_id integer,
    style_id integer,
    title character varying(100) NOT NULL,
    cover_url character varying(255) NOT NULL,
    pages character varying NOT NULL,
    enhanced_scenes character varying NOT NULL,
    original_paragraphs character varying NOT NULL,
    status character varying NOT NULL,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL,
    is_deleted boolean NOT NULL
);


--
-- Name: storybooks_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.storybooks_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: storybooks_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.storybooks_id_seq OWNED BY public.storybooks.id;


--
-- Name: subscription_plans; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.subscription_plans (
    id integer NOT NULL,
    name character varying(255) NOT NULL,
    description text,
    monthly_credits integer NOT NULL,
    price_usd_cents integer NOT NULL,
    stripe_price_id character varying(255) NOT NULL,
    stripe_product_id character varying(255),
    is_active boolean DEFAULT true,
    sort_order integer DEFAULT 0,
    created_at timestamp(6) without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp(6) without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: subscription_plans_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.subscription_plans_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    MAXVALUE 2147483647
    CACHE 1;


--
-- Name: subscription_plans_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.subscription_plans_id_seq OWNED BY public.subscription_plans.id;


--
-- Name: subscriptions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.subscriptions (
    id integer NOT NULL,
    user_id character varying(50) NOT NULL,
    plan_id integer NOT NULL,
    stripe_subscription_id character varying(255) DEFAULT NULL::character varying,
    order_id character varying(64) NOT NULL,
    stripe_customer_id character varying(64) DEFAULT ''::character varying,
    invoice_status character varying(16) DEFAULT ''::character varying,
    stripe_session_id character varying(255) DEFAULT ''::character varying,
    status character varying(16) DEFAULT 'init'::character varying NOT NULL,
    checkout_status character varying(50) DEFAULT ''::character varying NOT NULL,
    current_period_start timestamp(6) without time zone NOT NULL,
    current_period_end timestamp(6) without time zone NOT NULL,
    cancel_at_period_end boolean DEFAULT false,
    created_at timestamp(6) without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp(6) without time zone DEFAULT CURRENT_TIMESTAMP,
    canceled_at timestamp(6) without time zone,
    is_deleted boolean DEFAULT false,
    stripe_invoice_id character varying(255) DEFAULT ''::character varying
);


--
-- Name: subscriptions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.subscriptions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    MAXVALUE 2147483647
    CACHE 1;


--
-- Name: subscriptions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.subscriptions_id_seq OWNED BY public.subscriptions.id;


--
-- Name: task_executions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.task_executions (
    id integer NOT NULL,
    task_id character varying NOT NULL,
    status public.taskstatus NOT NULL,
    start_time timestamp without time zone NOT NULL,
    end_time timestamp without time zone,
    duration double precision,
    result json,
    error_message text,
    trigger_type character varying NOT NULL,
    triggered_by character varying,
    created_at timestamp without time zone NOT NULL
);


--
-- Name: task_executions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.task_executions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: task_executions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.task_executions_id_seq OWNED BY public.task_executions.id;


--
-- Name: user_credits; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_credits (
    id integer NOT NULL,
    user_id character varying(50) NOT NULL,
    balance integer NOT NULL,
    total_earned integer NOT NULL,
    total_used integer NOT NULL,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL,
    is_deleted boolean NOT NULL,
    subscription_balance integer DEFAULT 0 NOT NULL,
    purchased_balance integer DEFAULT 0 NOT NULL
);


--
-- Name: user_credits_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.user_credits_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: user_credits_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.user_credits_id_seq OWNED BY public.user_credits.id;


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    id integer NOT NULL,
    user_id character varying(50) NOT NULL,
    username character varying(50),
    email character varying(100),
    phone character varying(20),
    password_hash character varying(255),
    auth_type character varying(20) NOT NULL,
    stripe_customer_id character varying(64) DEFAULT ''::character varying,
    is_admin boolean NOT NULL,
    status character varying(20) NOT NULL,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL,
    is_deleted boolean NOT NULL,
    avatar_url text
);


--
-- Name: users_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.users_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: users_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.users_id_seq OWNED BY public.users.id;


--
-- Name: video_analysis; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.video_analysis (
    id integer NOT NULL,
    uuid character varying NOT NULL,
    video_type character varying NOT NULL,
    duration integer,
    main_character character varying NOT NULL,
    purpose character varying NOT NULL,
    key_elements text,
    style_preferences text,
    target_audience character varying,
    next_action character varying NOT NULL,
    run_id character varying NOT NULL,
    user_id character varying NOT NULL,
    conversation_id character varying NOT NULL,
    thread_id character varying NOT NULL,
    additional_data json,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL,
    duration_sec double precision,
    content_category character varying(64) DEFAULT NULL::character varying,
    hidden_style_description text,
    curated_style_prompt_id character varying(36) DEFAULT NULL::character varying
);


--
-- Name: video_analysis_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.video_analysis_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: video_analysis_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.video_analysis_id_seq OWNED BY public.video_analysis.id;


--
-- Name: video_assemblies; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.video_assemblies (
    id integer NOT NULL,
    uuid character varying NOT NULL,
    conversation_id character varying NOT NULL,
    thread_id character varying NOT NULL,
    run_id character varying NOT NULL,
    user_id character varying NOT NULL,
    story_outline_id character varying NOT NULL,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL,
    final_video_url text,
    total_duration double precision NOT NULL,
    success boolean NOT NULL,
    error_msg text,
    video_urls json,
    music_url text,
    audio_url text,
    additional_data json,
    source_video_versions json,
    source_narration_versions json,
    source_audio_effect_versions json,
    uploaded_audio_files json,
    source_video_urls json,
    source_narration_urls json,
    source_audio_effect_urls json,
    assembly_mode character varying,
    has_background_music boolean DEFAULT false,
    mixed_with_background_audio boolean DEFAULT false,
    source_music_versions json,
    source_music_urls json,
    title character varying,
    raw_error_msg text,
    final_video_url_no_subtitle text
);


--
-- Name: video_assemblies_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.video_assemblies_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: video_assemblies_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.video_assemblies_id_seq OWNED BY public.video_assemblies.id;


--
-- Name: video_audio_effect_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.video_audio_effect_versions (
    id integer NOT NULL,
    uuid character varying NOT NULL,
    audio_effect_id character varying NOT NULL,
    conversation_id character varying NOT NULL,
    thread_id character varying NOT NULL,
    run_id character varying NOT NULL,
    user_id character varying NOT NULL,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL,
    version_number integer NOT NULL,
    shot_number integer NOT NULL,
    video_url text,
    audio_prompt text,
    enhanced_prompt text,
    audio_url text,
    provider character varying NOT NULL,
    duration double precision,
    is_bridge boolean NOT NULL,
    success boolean NOT NULL,
    error_msg text,
    params json,
    additional_data json,
    video_with_audio_url text,
    ai_messages text,
    raw_error_msg text,
    audio_effect_url text,
    model character varying,
    prompt text
);


--
-- Name: video_audio_effect_versions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.video_audio_effect_versions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: video_audio_effect_versions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.video_audio_effect_versions_id_seq OWNED BY public.video_audio_effect_versions.id;


--
-- Name: video_audio_effects; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.video_audio_effects (
    id integer NOT NULL,
    uuid character varying NOT NULL,
    conversation_id character varying NOT NULL,
    thread_id character varying NOT NULL,
    run_id character varying NOT NULL,
    user_id character varying NOT NULL,
    story_outline_id character varying NOT NULL,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL,
    scene_id character varying NOT NULL,
    storyboard_detail_id character varying NOT NULL,
    detailed_shot_id character varying NOT NULL,
    video_generation_id character varying NOT NULL,
    shot_number integer NOT NULL,
    is_bridge boolean NOT NULL,
    has_audio_effect boolean NOT NULL,
    current_version_index integer NOT NULL,
    additional_data json,
    audio_segment_id character varying
);


--
-- Name: video_audio_effects_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.video_audio_effects_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: video_audio_effects_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.video_audio_effects_id_seq OWNED BY public.video_audio_effects.id;


--
-- Name: video_audio_section; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.video_audio_section (
    id bigint NOT NULL,
    uuid uuid NOT NULL,
    user_id character varying(255),
    conversation_id character varying(255),
    thread_id character varying(255),
    run_id character varying(255),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    transcription_uuid uuid NOT NULL,
    section_type character varying(128) NOT NULL,
    start_time numeric(12,3) NOT NULL,
    end_time numeric(12,3) NOT NULL,
    musical_features text,
    section_emotion character varying(128),
    suggested_visual_intensity character varying(128),
    suggested_rhythmic_strategy character varying(128),
    suggested_visual_theme character varying(256),
    suggested_context text,
    additional_data jsonb
);


--
-- Name: video_audio_section_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.video_audio_section_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: video_audio_section_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.video_audio_section_id_seq OWNED BY public.video_audio_section.id;


--
-- Name: video_audio_segment; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.video_audio_segment (
    id integer NOT NULL,
    uuid character varying NOT NULL,
    transcription_uuid character varying NOT NULL,
    segment_id integer NOT NULL,
    start double precision NOT NULL,
    "end" double precision NOT NULL,
    duration double precision NOT NULL,
    text text,
    run_id character varying NOT NULL,
    user_id character varying NOT NULL,
    conversation_id character varying NOT NULL,
    thread_id character varying NOT NULL,
    additional_data json,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL,
    emotion character varying,
    tempo character varying,
    vocal_gender character varying(8) DEFAULT NULL::character varying,
    vocal_presence boolean
);


--
-- Name: video_audio_segment_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.video_audio_segment_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: video_audio_segment_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.video_audio_segment_id_seq OWNED BY public.video_audio_segment.id;


--
-- Name: video_audio_transcription; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.video_audio_transcription (
    id integer NOT NULL,
    uuid character varying NOT NULL,
    task character varying NOT NULL,
    language character varying NOT NULL,
    duration double precision NOT NULL,
    text text,
    audio_url character varying NOT NULL,
    run_id character varying NOT NULL,
    user_id character varying NOT NULL,
    conversation_id character varying NOT NULL,
    thread_id character varying NOT NULL,
    additional_data json,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL,
    filename character varying,
    is_instrumental boolean DEFAULT false NOT NULL,
    song_name character varying(512) DEFAULT NULL::character varying,
    global_bpm numeric(10,2) DEFAULT NULL::numeric,
    genre character varying(128) DEFAULT NULL::character varying,
    global_emotion character varying(128) DEFAULT NULL::character varying,
    suggested_global_theme text,
    suggested_color_palette character varying(256) DEFAULT NULL::character varying
);


--
-- Name: video_audio_transcription_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.video_audio_transcription_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: video_audio_transcription_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.video_audio_transcription_id_seq OWNED BY public.video_audio_transcription.id;


--
-- Name: video_chapter_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.video_chapter_versions (
    id bigint NOT NULL,
    uuid uuid NOT NULL,
    chapter_id uuid NOT NULL,
    version_number integer NOT NULL,
    user_id character varying(64),
    conversation_id character varying(64),
    thread_id character varying(64),
    run_id character varying(64),
    story_outline_id uuid,
    title text,
    description text,
    duration numeric(12,3),
    "order" integer,
    audio_segment_ids jsonb,
    additional_data jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: video_chapter_versions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.video_chapter_versions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: video_chapter_versions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.video_chapter_versions_id_seq OWNED BY public.video_chapter_versions.id;


--
-- Name: video_chapters; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.video_chapters (
    id integer NOT NULL,
    uuid character varying NOT NULL,
    conversation_id character varying NOT NULL,
    thread_id character varying NOT NULL,
    run_id character varying NOT NULL,
    story_outline_id character varying NOT NULL,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL,
    title text,
    description text,
    duration double precision NOT NULL,
    "order" integer NOT NULL,
    additional_data json,
    user_id character varying DEFAULT 'admin'::character varying NOT NULL,
    audio_segment_ids json,
    audio_section_uuid uuid,
    current_version_index integer DEFAULT 0
);


--
-- Name: video_chapters_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.video_chapters_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: video_chapters_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.video_chapters_id_seq OWNED BY public.video_chapters.id;


--
-- Name: video_character_edit_records; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.video_character_edit_records (
    id integer NOT NULL,
    uuid character varying NOT NULL,
    task_record_id character varying,
    character_id character varying NOT NULL,
    user_id character varying NOT NULL,
    conversation_id character varying NOT NULL,
    thread_id character varying NOT NULL,
    run_id character varying NOT NULL,
    character_name character varying NOT NULL,
    old_version_id character varying NOT NULL,
    new_version_id character varying NOT NULL,
    old_version_number integer NOT NULL,
    new_version_number integer NOT NULL,
    old_image_url text,
    new_image_url text,
    model character varying NOT NULL,
    old_prompt text,
    new_prompt text,
    user_action character varying NOT NULL,
    user_feedback text,
    edit_instruction text,
    success boolean NOT NULL,
    error_msg text,
    created_at timestamp without time zone NOT NULL
);


--
-- Name: video_character_edit_records_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.video_character_edit_records_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: video_character_edit_records_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.video_character_edit_records_id_seq OWNED BY public.video_character_edit_records.id;


--
-- Name: video_character_fusion_images; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.video_character_fusion_images (
    id integer NOT NULL,
    uuid character varying NOT NULL,
    user_id character varying NOT NULL,
    conversation_id character varying NOT NULL,
    thread_id character varying NOT NULL,
    run_id character varying NOT NULL,
    fusion_key character varying NOT NULL,
    image_type character varying NOT NULL,
    character_ids jsonb NOT NULL,
    fusion_image_url text,
    aspect_ratio character varying,
    resolution character varying,
    model character varying,
    seed integer,
    provider character varying,
    fusion_prompt text,
    success boolean DEFAULT true NOT NULL,
    error_msg text,
    raw_error_msg text,
    ai_messages text,
    additional_data jsonb,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);


--
-- Name: video_character_fusion_images_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.video_character_fusion_images_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: video_character_fusion_images_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.video_character_fusion_images_id_seq OWNED BY public.video_character_fusion_images.id;


--
-- Name: video_character_generation_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.video_character_generation_versions (
    id integer NOT NULL,
    uuid character varying NOT NULL,
    video_character_id character varying NOT NULL,
    conversation_id character varying NOT NULL,
    thread_id character varying NOT NULL,
    run_id character varying NOT NULL,
    user_id character varying NOT NULL,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL,
    version_number integer NOT NULL,
    character_image_url text,
    t2i_prompt text,
    provider character varying NOT NULL,
    reference_image_urls json,
    success boolean NOT NULL,
    error_msg text,
    ai_messages text,
    additional_data json,
    raw_error_msg text,
    image_tool_metrics jsonb,
    tool_duration_sec double precision,
    tool_cost double precision,
    aspect_ratio character varying,
    resolution character varying,
    model character varying,
    seed integer,
    image_generation_tool character varying,
    multi_view_image_version_id character varying,
    selected_multi_view_version_id character varying
);


--
-- Name: video_character_generation_versions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.video_character_generation_versions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: video_character_generation_versions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.video_character_generation_versions_id_seq OWNED BY public.video_character_generation_versions.id;


--
-- Name: video_character_multi_view_image_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.video_character_multi_view_image_versions (
    id integer NOT NULL,
    uuid character varying NOT NULL,
    multi_view_image_id character varying NOT NULL,
    video_character_id character varying NOT NULL,
    video_character_version_id character varying,
    user_id character varying NOT NULL,
    conversation_id character varying NOT NULL,
    thread_id character varying NOT NULL,
    run_id character varying NOT NULL,
    version_number integer NOT NULL,
    multi_view_image_url text,
    multi_view_prompt text,
    provider character varying,
    success boolean DEFAULT true NOT NULL,
    error_msg text,
    raw_error_msg text,
    model character varying,
    aspect_ratio character varying,
    resolution character varying,
    seed integer,
    ai_messages text,
    additional_data json,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);


--
-- Name: video_character_multi_view_image_versions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.video_character_multi_view_image_versions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: video_character_multi_view_image_versions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.video_character_multi_view_image_versions_id_seq OWNED BY public.video_character_multi_view_image_versions.id;


--
-- Name: video_character_multi_view_images; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.video_character_multi_view_images (
    id integer NOT NULL,
    uuid character varying NOT NULL,
    video_character_id character varying NOT NULL,
    user_id character varying NOT NULL,
    conversation_id character varying NOT NULL,
    thread_id character varying NOT NULL,
    run_id character varying NOT NULL,
    current_version_index integer DEFAULT 0 NOT NULL,
    additional_data json,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);


--
-- Name: video_character_multi_view_images_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.video_character_multi_view_images_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: video_character_multi_view_images_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.video_character_multi_view_images_id_seq OWNED BY public.video_character_multi_view_images.id;


--
-- Name: video_characters; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.video_characters (
    id integer NOT NULL,
    uuid character varying NOT NULL,
    conversation_id character varying NOT NULL,
    thread_id character varying NOT NULL,
    run_id character varying NOT NULL,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL,
    name text,
    description text,
    personality text,
    appearance text,
    role text,
    image_url character varying,
    additional_data json,
    user_id character varying DEFAULT 'admin'::character varying NOT NULL,
    style text,
    body_type text,
    current_version_index integer DEFAULT 0 NOT NULL,
    type character varying(50) DEFAULT 'character'::character varying NOT NULL,
    selected_version_id character varying
);


--
-- Name: video_characters_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.video_characters_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: video_characters_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.video_characters_id_seq OWNED BY public.video_characters.id;


--
-- Name: video_detailed_shots; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.video_detailed_shots (
    id integer NOT NULL,
    uuid character varying NOT NULL,
    user_id character varying NOT NULL,
    conversation_id character varying NOT NULL,
    thread_id character varying NOT NULL,
    run_id character varying NOT NULL,
    storyboard_detail_id character varying NOT NULL,
    scene_id character varying NOT NULL,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL,
    shot_number integer NOT NULL,
    duration integer,
    shot_type text,
    scene_description text,
    camera_movement text,
    lighting text,
    visual_effects text,
    transition text,
    dialogue text,
    sound_effects text,
    is_bridge boolean NOT NULL,
    character_ids json,
    style_guide text,
    additional_data json,
    audio_segment_ids json,
    narration text,
    chapter_id character varying,
    keyframe_prompt text,
    camera_position text,
    camera_angle character varying(50),
    subject_angle character varying(50),
    subject_pose text,
    duration_sec double precision,
    generation_mode character varying(20) DEFAULT NULL::character varying,
    generation_routing jsonb
);


--
-- Name: video_detailed_shots_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.video_detailed_shots_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: video_detailed_shots_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.video_detailed_shots_id_seq OWNED BY public.video_detailed_shots.id;


--
-- Name: video_generation_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.video_generation_versions (
    id integer NOT NULL,
    uuid character varying NOT NULL,
    video_generation_id character varying NOT NULL,
    conversation_id character varying NOT NULL,
    thread_id character varying NOT NULL,
    run_id character varying NOT NULL,
    user_id character varying NOT NULL,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL,
    version_number integer NOT NULL,
    shot_number integer NOT NULL,
    video_url text,
    provider character varying NOT NULL,
    is_bridge boolean NOT NULL,
    success boolean NOT NULL,
    error_msg text,
    keyframe_url text,
    motion_prompt text,
    duration double precision,
    fps integer,
    additional_data json,
    audio_segment_ids json,
    ai_messages text,
    keyframe_version_ids jsonb,
    raw_error_msg text,
    video_tool_metrics jsonb,
    tool_duration_sec double precision,
    tool_cost double precision,
    generation_mode character varying(20) DEFAULT NULL::character varying,
    audio_url text,
    aspect_ratio character varying,
    resolution character varying,
    model character varying,
    seed integer,
    video_generation_tool character varying,
    negative_prompt text,
    style character varying,
    raw_generation_params json
);


--
-- Name: video_generation_versions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.video_generation_versions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: video_generation_versions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.video_generation_versions_id_seq OWNED BY public.video_generation_versions.id;


--
-- Name: video_generations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.video_generations (
    id integer NOT NULL,
    uuid character varying NOT NULL,
    conversation_id character varying NOT NULL,
    thread_id character varying NOT NULL,
    run_id character varying NOT NULL,
    user_id character varying NOT NULL,
    story_outline_id character varying NOT NULL,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL,
    scene_id character varying NOT NULL,
    storyboard_detail_id character varying NOT NULL,
    detailed_shot_id character varying NOT NULL,
    -- SD2 / reference_t2v 跳过关键帧时可为 NULL
    keyframe_id character varying,
    shot_number integer NOT NULL,
    is_bridge boolean NOT NULL,
    current_version_index integer NOT NULL,
    additional_data json,
    keyframe_ids jsonb
);


--
-- Name: video_generations_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.video_generations_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: video_generations_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.video_generations_id_seq OWNED BY public.video_generations.id;


--
-- Name: video_keyframe_reflection_results; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.video_keyframe_reflection_results (
    id integer NOT NULL,
    uuid character varying NOT NULL,
    reflection_id character varying NOT NULL,
    keyframe_id character varying NOT NULL,
    keyframe_version_id character varying NOT NULL,
    conversation_id character varying NOT NULL,
    thread_id character varying NOT NULL,
    run_id character varying NOT NULL,
    user_id character varying NOT NULL,
    shot_number integer NOT NULL,
    shot_uuid character varying NOT NULL,
    needs_regeneration boolean DEFAULT false NOT NULL,
    issues json,
    analysis_summary text,
    improvement_points json,
    original_description text,
    improved_description text,
    new_keyframe_version_id character varying,
    regeneration_success boolean,
    regeneration_error text,
    additional_data json,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);


--
-- Name: video_keyframe_reflection_results_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.video_keyframe_reflection_results_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: video_keyframe_reflection_results_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.video_keyframe_reflection_results_id_seq OWNED BY public.video_keyframe_reflection_results.id;


--
-- Name: video_keyframe_reflections; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.video_keyframe_reflections (
    id integer NOT NULL,
    uuid character varying NOT NULL,
    conversation_id character varying NOT NULL,
    thread_id character varying NOT NULL,
    run_id character varying NOT NULL,
    user_id character varying NOT NULL,
    story_outline_id character varying NOT NULL,
    iteration_number integer NOT NULL,
    total_keyframes integer NOT NULL,
    analyzed_keyframes integer NOT NULL,
    regenerated_keyframes integer NOT NULL,
    consistency_score double precision DEFAULT 0 NOT NULL,
    analysis_duration_seconds double precision DEFAULT 0 NOT NULL,
    regeneration_duration_seconds double precision DEFAULT 0 NOT NULL,
    additional_data json,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);


--
-- Name: video_keyframe_reflections_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.video_keyframe_reflections_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: video_keyframe_reflections_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.video_keyframe_reflections_id_seq OWNED BY public.video_keyframe_reflections.id;


--
-- Name: video_keyframe_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.video_keyframe_versions (
    id integer NOT NULL,
    uuid character varying NOT NULL,
    keyframe_id character varying NOT NULL,
    conversation_id character varying NOT NULL,
    thread_id character varying NOT NULL,
    run_id character varying NOT NULL,
    user_id character varying NOT NULL,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL,
    version_number integer NOT NULL,
    shot_number integer NOT NULL,
    keyframe_url text,
    t2i_prompt text,
    provider character varying NOT NULL,
    is_bridge boolean NOT NULL,
    reference_image_urls json,
    success boolean NOT NULL,
    error_msg text,
    additional_data json,
    audio_segment_ids json,
    ai_messages text,
    character_version_ids json,
    raw_error_msg text,
    image_tool_metrics jsonb,
    tool_duration_sec double precision,
    tool_cost double precision,
    aspect_ratio character varying,
    resolution character varying,
    model character varying,
    seed integer,
    image_generation_tool character varying
);


--
-- Name: video_keyframe_versions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.video_keyframe_versions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: video_keyframe_versions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.video_keyframe_versions_id_seq OWNED BY public.video_keyframe_versions.id;


--
-- Name: video_keyframes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.video_keyframes (
    id integer NOT NULL,
    uuid character varying NOT NULL,
    conversation_id character varying NOT NULL,
    thread_id character varying NOT NULL,
    run_id character varying NOT NULL,
    user_id character varying NOT NULL,
    story_outline_id character varying NOT NULL,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL,
    scene_id character varying NOT NULL,
    storyboard_detail_id character varying NOT NULL,
    detailed_shot_id character varying NOT NULL,
    shot_number integer NOT NULL,
    is_bridge boolean NOT NULL,
    reference_image_urls json,
    current_version_index integer NOT NULL,
    additional_data json,
    character_ids json,
    frame_index integer DEFAULT 0
);


--
-- Name: video_keyframes_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.video_keyframes_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: video_keyframes_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.video_keyframes_id_seq OWNED BY public.video_keyframes.id;


--
-- Name: video_lipsync_generation_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.video_lipsync_generation_versions (
    id integer NOT NULL,
    uuid character varying NOT NULL,
    lipsync_generation_id character varying NOT NULL,
    video_segment_id character varying NOT NULL,
    video_segment_version_id character varying NOT NULL,
    music_generation_version_id character varying NOT NULL,
    conversation_id character varying NOT NULL,
    thread_id character varying NOT NULL,
    run_id character varying NOT NULL,
    user_id character varying NOT NULL,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL,
    version_number integer NOT NULL,
    segment_number integer NOT NULL,
    video_url text,
    audio_url text,
    original_video_url text,
    provider character varying NOT NULL,
    model character varying NOT NULL,
    success boolean NOT NULL,
    error_msg text,
    duration double precision,
    params json,
    additional_data json,
    raw_error_msg text
);


--
-- Name: video_lipsync_generation_versions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.video_lipsync_generation_versions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: video_lipsync_generation_versions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.video_lipsync_generation_versions_id_seq OWNED BY public.video_lipsync_generation_versions.id;


--
-- Name: video_lipsync_generations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.video_lipsync_generations (
    id integer NOT NULL,
    uuid character varying NOT NULL,
    conversation_id character varying NOT NULL,
    thread_id character varying NOT NULL,
    run_id character varying NOT NULL,
    user_id character varying NOT NULL,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL,
    video_segment_id character varying NOT NULL,
    music_generation_id character varying NOT NULL,
    segment_number integer NOT NULL,
    current_version_index integer NOT NULL,
    additional_data json
);


--
-- Name: video_lipsync_generations_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.video_lipsync_generations_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: video_lipsync_generations_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.video_lipsync_generations_id_seq OWNED BY public.video_lipsync_generations.id;


--
-- Name: video_music_generation_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.video_music_generation_versions (
    id integer NOT NULL,
    uuid character varying NOT NULL,
    music_generation_id character varying NOT NULL,
    conversation_id character varying NOT NULL,
    thread_id character varying NOT NULL,
    run_id character varying NOT NULL,
    user_id character varying NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    version_number integer NOT NULL,
    shot_number integer,
    music_url text NOT NULL,
    music_prompt text,
    provider character varying DEFAULT 'suno'::character varying NOT NULL,
    duration double precision NOT NULL,
    success boolean DEFAULT true,
    error_msg text,
    audio_transcription_id character varying,
    audio_segment_ids json,
    params json,
    ai_messages text,
    additional_data json,
    original_audio_url text,
    segment_start_time double precision,
    segment_end_time double precision,
    is_instrumental boolean DEFAULT true,
    raw_error_msg text,
    model character varying,
    aspect_ratio character varying,
    resolution character varying,
    seed integer
);


--
-- Name: video_music_generation_versions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.video_music_generation_versions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: video_music_generation_versions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.video_music_generation_versions_id_seq OWNED BY public.video_music_generation_versions.id;


--
-- Name: video_music_generations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.video_music_generations (
    id integer NOT NULL,
    uuid character varying NOT NULL,
    conversation_id character varying NOT NULL,
    thread_id character varying NOT NULL,
    run_id character varying NOT NULL,
    user_id character varying NOT NULL,
    story_outline_id character varying,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL,
    additional_data json,
    scene_id character varying,
    shot_number integer,
    is_full_story_music boolean DEFAULT false,
    is_instrumental boolean DEFAULT true,
    current_version_index integer DEFAULT 0
);


--
-- Name: video_music_generations_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.video_music_generations_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: video_music_generations_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.video_music_generations_id_seq OWNED BY public.video_music_generations.id;


--
-- Name: video_narration_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.video_narration_versions (
    id integer NOT NULL,
    uuid character varying NOT NULL,
    narration_id character varying NOT NULL,
    conversation_id character varying NOT NULL,
    thread_id character varying NOT NULL,
    run_id character varying NOT NULL,
    user_id character varying NOT NULL,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL,
    version_number integer NOT NULL,
    shot_number integer NOT NULL,
    narration_text text,
    enhanced_prompt text,
    audio_url text,
    provider character varying NOT NULL,
    duration double precision,
    is_bridge boolean NOT NULL,
    success boolean NOT NULL,
    error_msg text,
    params json,
    additional_data json,
    ai_messages text,
    raw_error_msg text,
    narration_url text,
    model character varying,
    voice character varying,
    text text
);


--
-- Name: video_narration_versions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.video_narration_versions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: video_narration_versions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.video_narration_versions_id_seq OWNED BY public.video_narration_versions.id;


--
-- Name: video_narrations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.video_narrations (
    id integer NOT NULL,
    uuid character varying NOT NULL,
    conversation_id character varying NOT NULL,
    thread_id character varying NOT NULL,
    run_id character varying NOT NULL,
    user_id character varying NOT NULL,
    story_outline_id character varying NOT NULL,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL,
    scene_id character varying NOT NULL,
    storyboard_detail_id character varying NOT NULL,
    detailed_shot_id character varying NOT NULL,
    shot_number integer NOT NULL,
    is_bridge boolean NOT NULL,
    has_narration boolean NOT NULL,
    current_version_index integer NOT NULL,
    additional_data json,
    audio_segment_id character varying
);


--
-- Name: video_narrations_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.video_narrations_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: video_narrations_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.video_narrations_id_seq OWNED BY public.video_narrations.id;


--
-- Name: video_scene_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.video_scene_versions (
    id bigint NOT NULL,
    uuid uuid NOT NULL,
    scene_id uuid NOT NULL,
    version_number integer NOT NULL,
    user_id character varying(64),
    conversation_id character varying(64),
    thread_id character varying(64),
    run_id character varying(64),
    scene_number integer,
    title text,
    description text,
    duration integer,
    camera_angle text,
    character_action text,
    visual_style text,
    transition_style text,
    is_bridge boolean,
    character_ids jsonb,
    audio_segment_ids jsonb,
    chapter_id uuid,
    generation_mode character varying(32),
    additional_data jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: video_scene_versions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.video_scene_versions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: video_scene_versions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.video_scene_versions_id_seq OWNED BY public.video_scene_versions.id;


--
-- Name: video_scenes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.video_scenes (
    id integer NOT NULL,
    uuid character varying NOT NULL,
    conversation_id character varying NOT NULL,
    thread_id character varying NOT NULL,
    run_id character varying NOT NULL,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL,
    scene_number integer NOT NULL,
    title text,
    description text,
    duration integer,
    camera_angle text,
    character_action text,
    visual_style text,
    transition_style text,
    is_bridge boolean NOT NULL,
    character_ids json,
    chapter_id character varying,
    additional_data json,
    user_id character varying DEFAULT 'admin'::character varying NOT NULL,
    audio_segment_ids json,
    duration_sec double precision,
    generation_mode character varying(20) DEFAULT NULL::character varying,
    current_version_index integer DEFAULT 0
);


--
-- Name: video_scenes_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.video_scenes_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: video_scenes_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.video_scenes_id_seq OWNED BY public.video_scenes.id;


--
-- Name: video_segment_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.video_segment_versions (
    id integer NOT NULL,
    uuid character varying NOT NULL,
    video_segment_id character varying NOT NULL,
    conversation_id character varying NOT NULL,
    thread_id character varying NOT NULL,
    run_id character varying NOT NULL,
    user_id character varying NOT NULL,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL,
    version_number integer NOT NULL,
    segment_number integer NOT NULL,
    video_url text,
    success boolean NOT NULL,
    error_msg text,
    video_generation_version_ids json,
    narration_version_ids json,
    keyframe_version_ids json,
    music_generation_version_id character varying,
    audio_effect_version_id character varying,
    lipsync_version_id character varying,
    duration double precision,
    fps integer,
    additional_data json,
    raw_error_msg text
);


--
-- Name: video_segment_versions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.video_segment_versions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: video_segment_versions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.video_segment_versions_id_seq OWNED BY public.video_segment_versions.id;


--
-- Name: video_segments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.video_segments (
    id integer NOT NULL,
    uuid character varying NOT NULL,
    conversation_id character varying NOT NULL,
    thread_id character varying NOT NULL,
    run_id character varying NOT NULL,
    user_id character varying NOT NULL,
    story_outline_id character varying NOT NULL,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL,
    segment_number integer NOT NULL,
    video_generation_ids json,
    narration_ids json,
    keyframe_ids json,
    scene_ids json,
    storyboard_detail_ids json,
    music_generation_id character varying,
    audio_effect_id character varying,
    lipsync_id character varying,
    current_version_index integer NOT NULL,
    additional_data json
);


--
-- Name: video_segments_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.video_segments_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: video_segments_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.video_segments_id_seq OWNED BY public.video_segments.id;


--
-- Name: video_shot_edit_records; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.video_shot_edit_records (
    id integer NOT NULL,
    uuid character varying NOT NULL,
    task_record_id character varying,
    video_generation_id character varying NOT NULL,
    user_id character varying NOT NULL,
    conversation_id character varying NOT NULL,
    thread_id character varying NOT NULL,
    run_id character varying NOT NULL,
    shot_number integer NOT NULL,
    old_version_id character varying NOT NULL,
    new_version_id character varying NOT NULL,
    old_version_number integer NOT NULL,
    new_version_number integer NOT NULL,
    old_video_url text,
    new_video_url text,
    duration double precision,
    model character varying NOT NULL,
    old_prompt text,
    new_prompt text,
    user_action character varying NOT NULL,
    user_feedback text,
    edit_instruction text,
    success boolean NOT NULL,
    error_msg text,
    created_at timestamp without time zone NOT NULL,
    cascaded_from_storyboard_edit_id character varying
);


--
-- Name: video_shot_edit_records_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.video_shot_edit_records_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: video_shot_edit_records_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.video_shot_edit_records_id_seq OWNED BY public.video_shot_edit_records.id;


--
-- Name: video_story_outline; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.video_story_outline (
    id integer NOT NULL,
    uuid character varying NOT NULL,
    title character varying NOT NULL,
    theme character varying NOT NULL,
    description text,
    key_message text,
    total_duration integer,
    style_guide text,
    structure text,
    run_id character varying NOT NULL,
    user_id character varying NOT NULL,
    conversation_id character varying NOT NULL,
    thread_id character varying NOT NULL,
    additional_data json,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL,
    analysis_id character varying,
    total_duration_sec double precision,
    audio_transcription_uuid uuid,
    current_version_index integer DEFAULT 0
);


--
-- Name: video_story_outline_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.video_story_outline_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: video_story_outline_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.video_story_outline_id_seq OWNED BY public.video_story_outline.id;


--
-- Name: video_story_outline_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.video_story_outline_versions (
    id bigint NOT NULL,
    uuid uuid NOT NULL,
    story_outline_id uuid NOT NULL,
    version_number integer NOT NULL,
    user_id character varying(64),
    conversation_id character varying(64),
    thread_id character varying(64),
    run_id character varying(64),
    title text,
    description text,
    theme text,
    key_message text,
    total_duration integer,
    style_guide text,
    analysis_id character varying(64),
    additional_data jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: video_story_outline_versions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.video_story_outline_versions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: video_story_outline_versions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.video_story_outline_versions_id_seq OWNED BY public.video_story_outline_versions.id;


--
-- Name: video_storyboard_details; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.video_storyboard_details (
    id integer NOT NULL,
    uuid character varying NOT NULL,
    user_id character varying NOT NULL,
    conversation_id character varying NOT NULL,
    thread_id character varying NOT NULL,
    run_id character varying NOT NULL,
    story_outline_id character varying NOT NULL,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL,
    total_duration integer,
    visual_style text,
    shots_count integer NOT NULL,
    additional_data json,
    camera_position text,
    camera_angle character varying(50),
    subject_angle character varying(50),
    subject_pose text,
    total_duration_sec double precision
);


--
-- Name: video_storyboard_details_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.video_storyboard_details_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: video_storyboard_details_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.video_storyboard_details_id_seq OWNED BY public.video_storyboard_details.id;


--
-- Name: video_storyboard_edit_records; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.video_storyboard_edit_records (
    id integer NOT NULL,
    uuid character varying NOT NULL,
    task_record_id character varying,
    keyframe_id character varying NOT NULL,
    user_id character varying NOT NULL,
    conversation_id character varying NOT NULL,
    thread_id character varying NOT NULL,
    run_id character varying NOT NULL,
    shot_number integer NOT NULL,
    old_version_id character varying NOT NULL,
    new_version_id character varying NOT NULL,
    old_version_number integer NOT NULL,
    new_version_number integer NOT NULL,
    old_image_url text,
    new_image_url text,
    model character varying NOT NULL,
    old_prompt text,
    new_prompt text,
    user_action character varying NOT NULL,
    user_feedback text,
    edit_instruction text,
    success boolean NOT NULL,
    error_msg text,
    created_at timestamp without time zone NOT NULL
);


--
-- Name: video_storyboard_edit_records_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.video_storyboard_edit_records_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: video_storyboard_edit_records_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.video_storyboard_edit_records_id_seq OWNED BY public.video_storyboard_edit_records.id;


--
-- Name: video_task_records; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.video_task_records (
    id integer NOT NULL,
    uuid character varying NOT NULL,
    task_id character varying NOT NULL,
    conversation_id character varying NOT NULL,
    thread_id character varying NOT NULL,
    user_id character varying NOT NULL,
    detected_language character varying,
    generation_config json,
    actual_target_duration integer,
    task_input text,
    user_input_data json,
    task_start_time timestamp without time zone NOT NULL,
    task_finish_time timestamp without time zone,
    task_status character varying NOT NULL,
    analysis_uuid character varying,
    audio_transcription_uuids json,
    story_outline_uuid character varying,
    character_uuids json,
    scene_uuids json,
    shot_uuids json,
    keyframe_uuids json,
    narration_uuids json,
    audio_effect_uuids json,
    video_generation_uuids json,
    music_generation_uuids json,
    video_segments_uuids json,
    video_assembly_uuid character varying,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL,
    final_video_url text,
    billing_status character varying(32) DEFAULT NULL::character varying,
    langsmith_cost numeric(19,6) DEFAULT NULL::numeric,
    cost numeric(19,6) DEFAULT NULL::numeric,
    cost_calculated boolean,
    credits_deducted boolean,
    credits_amount integer,
    actual_target_duration_sec double precision,
    tool_consistency_summary jsonb
);


--
-- Name: video_task_records_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.video_task_records_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: video_task_records_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.video_task_records_id_seq OWNED BY public.video_task_records.id;


--
-- Name: app_configs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.app_configs ALTER COLUMN id SET DEFAULT nextval('public.app_configs_id_seq'::regclass);


--
-- Name: character_folders id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.character_folders ALTER COLUMN id SET DEFAULT nextval('public.character_folders_id_seq'::regclass);


--
-- Name: characters id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.characters ALTER COLUMN id SET DEFAULT nextval('public.characters_id_seq'::regclass);


--
-- Name: characters_v2 id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.characters_v2 ALTER COLUMN id SET DEFAULT nextval('public.characters_v2_id_seq'::regclass);


--
-- Name: conversation_messages id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversation_messages ALTER COLUMN id SET DEFAULT nextval('public.conversation_messages_id_seq'::regclass);


--
-- Name: conversation_states id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversation_states ALTER COLUMN id SET DEFAULT nextval('public.conversation_states_id_seq'::regclass);


--
-- Name: conversations id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversations ALTER COLUMN id SET DEFAULT nextval('public.conversations_id_seq'::regclass);


--
-- Name: creation_favorites id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.creation_favorites ALTER COLUMN id SET DEFAULT nextval('public.creation_favorites_id_seq'::regclass);


--
-- Name: creation_likes id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.creation_likes ALTER COLUMN id SET DEFAULT nextval('public.creation_likes_id_seq'::regclass);


--
-- Name: creations id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.creations ALTER COLUMN id SET DEFAULT nextval('public.creations_id_seq'::regclass);


--
-- Name: credit_allocations id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.credit_allocations ALTER COLUMN id SET DEFAULT nextval('public.credit_allocations_id_seq'::regclass);


--
-- Name: credit_history id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.credit_history ALTER COLUMN id SET DEFAULT nextval('public.credit_history_id_seq'::regclass);


--
-- Name: curated_style_prompts id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.curated_style_prompts ALTER COLUMN id SET DEFAULT nextval('public.curated_style_prompts_id_seq'::regclass);


--
-- Name: email_verifications id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.email_verifications ALTER COLUMN id SET DEFAULT nextval('public.email_verifications_id_seq'::regclass);


--
-- Name: invite_code_quotas id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invite_code_quotas ALTER COLUMN id SET DEFAULT nextval('public.invite_code_quotas_id_seq'::regclass);


--
-- Name: invite_codes id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invite_codes ALTER COLUMN id SET DEFAULT nextval('public.invite_codes_id_seq'::regclass);


--
-- Name: llm_api_logs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_api_logs ALTER COLUMN id SET DEFAULT nextval('public.llm_api_logs_id_seq'::regclass);


--
-- Name: paymentpackage id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.paymentpackage ALTER COLUMN id SET DEFAULT nextval('public.paymentpackage_id_seq'::regclass);


--
-- Name: paymentsettings id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.paymentsettings ALTER COLUMN id SET DEFAULT nextval('public.paymentsettings_id_seq'::regclass);


--
-- Name: paymenttransaction id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.paymenttransaction ALTER COLUMN id SET DEFAULT nextval('public.paymenttransaction_id_seq'::regclass);


--
-- Name: share_links id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.share_links ALTER COLUMN id SET DEFAULT nextval('public.share_links_id_seq'::regclass);


--
-- Name: stories id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.stories ALTER COLUMN id SET DEFAULT nextval('public.stories_id_seq'::regclass);


--
-- Name: storybook_styles id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.storybook_styles ALTER COLUMN id SET DEFAULT nextval('public.storybook_styles_id_seq'::regclass);


--
-- Name: storybooks id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.storybooks ALTER COLUMN id SET DEFAULT nextval('public.storybooks_id_seq'::regclass);


--
-- Name: subscription_plans id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subscription_plans ALTER COLUMN id SET DEFAULT nextval('public.subscription_plans_id_seq'::regclass);


--
-- Name: subscriptions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subscriptions ALTER COLUMN id SET DEFAULT nextval('public.subscriptions_id_seq'::regclass);


--
-- Name: task_executions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_executions ALTER COLUMN id SET DEFAULT nextval('public.task_executions_id_seq'::regclass);


--
-- Name: user_credits id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_credits ALTER COLUMN id SET DEFAULT nextval('public.user_credits_id_seq'::regclass);


--
-- Name: users id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users ALTER COLUMN id SET DEFAULT nextval('public.users_id_seq'::regclass);


--
-- Name: video_analysis id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_analysis ALTER COLUMN id SET DEFAULT nextval('public.video_analysis_id_seq'::regclass);


--
-- Name: video_assemblies id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_assemblies ALTER COLUMN id SET DEFAULT nextval('public.video_assemblies_id_seq'::regclass);


--
-- Name: video_audio_effect_versions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_audio_effect_versions ALTER COLUMN id SET DEFAULT nextval('public.video_audio_effect_versions_id_seq'::regclass);


--
-- Name: video_audio_effects id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_audio_effects ALTER COLUMN id SET DEFAULT nextval('public.video_audio_effects_id_seq'::regclass);


--
-- Name: video_audio_section id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_audio_section ALTER COLUMN id SET DEFAULT nextval('public.video_audio_section_id_seq'::regclass);


--
-- Name: video_audio_segment id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_audio_segment ALTER COLUMN id SET DEFAULT nextval('public.video_audio_segment_id_seq'::regclass);


--
-- Name: video_audio_transcription id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_audio_transcription ALTER COLUMN id SET DEFAULT nextval('public.video_audio_transcription_id_seq'::regclass);


--
-- Name: video_chapter_versions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_chapter_versions ALTER COLUMN id SET DEFAULT nextval('public.video_chapter_versions_id_seq'::regclass);


--
-- Name: video_chapters id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_chapters ALTER COLUMN id SET DEFAULT nextval('public.video_chapters_id_seq'::regclass);


--
-- Name: video_character_edit_records id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_character_edit_records ALTER COLUMN id SET DEFAULT nextval('public.video_character_edit_records_id_seq'::regclass);


--
-- Name: video_character_fusion_images id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_character_fusion_images ALTER COLUMN id SET DEFAULT nextval('public.video_character_fusion_images_id_seq'::regclass);


--
-- Name: video_character_generation_versions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_character_generation_versions ALTER COLUMN id SET DEFAULT nextval('public.video_character_generation_versions_id_seq'::regclass);


--
-- Name: video_character_multi_view_image_versions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_character_multi_view_image_versions ALTER COLUMN id SET DEFAULT nextval('public.video_character_multi_view_image_versions_id_seq'::regclass);


--
-- Name: video_character_multi_view_images id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_character_multi_view_images ALTER COLUMN id SET DEFAULT nextval('public.video_character_multi_view_images_id_seq'::regclass);


--
-- Name: video_characters id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_characters ALTER COLUMN id SET DEFAULT nextval('public.video_characters_id_seq'::regclass);


--
-- Name: video_detailed_shots id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_detailed_shots ALTER COLUMN id SET DEFAULT nextval('public.video_detailed_shots_id_seq'::regclass);


--
-- Name: video_generation_versions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_generation_versions ALTER COLUMN id SET DEFAULT nextval('public.video_generation_versions_id_seq'::regclass);


--
-- Name: video_generations id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_generations ALTER COLUMN id SET DEFAULT nextval('public.video_generations_id_seq'::regclass);


--
-- Name: video_keyframe_reflection_results id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_keyframe_reflection_results ALTER COLUMN id SET DEFAULT nextval('public.video_keyframe_reflection_results_id_seq'::regclass);


--
-- Name: video_keyframe_reflections id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_keyframe_reflections ALTER COLUMN id SET DEFAULT nextval('public.video_keyframe_reflections_id_seq'::regclass);


--
-- Name: video_keyframe_versions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_keyframe_versions ALTER COLUMN id SET DEFAULT nextval('public.video_keyframe_versions_id_seq'::regclass);


--
-- Name: video_keyframes id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_keyframes ALTER COLUMN id SET DEFAULT nextval('public.video_keyframes_id_seq'::regclass);


--
-- Name: video_lipsync_generation_versions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_lipsync_generation_versions ALTER COLUMN id SET DEFAULT nextval('public.video_lipsync_generation_versions_id_seq'::regclass);


--
-- Name: video_lipsync_generations id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_lipsync_generations ALTER COLUMN id SET DEFAULT nextval('public.video_lipsync_generations_id_seq'::regclass);


--
-- Name: video_music_generation_versions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_music_generation_versions ALTER COLUMN id SET DEFAULT nextval('public.video_music_generation_versions_id_seq'::regclass);


--
-- Name: video_music_generations id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_music_generations ALTER COLUMN id SET DEFAULT nextval('public.video_music_generations_id_seq'::regclass);


--
-- Name: video_narration_versions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_narration_versions ALTER COLUMN id SET DEFAULT nextval('public.video_narration_versions_id_seq'::regclass);


--
-- Name: video_narrations id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_narrations ALTER COLUMN id SET DEFAULT nextval('public.video_narrations_id_seq'::regclass);


--
-- Name: video_scene_versions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_scene_versions ALTER COLUMN id SET DEFAULT nextval('public.video_scene_versions_id_seq'::regclass);


--
-- Name: video_scenes id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_scenes ALTER COLUMN id SET DEFAULT nextval('public.video_scenes_id_seq'::regclass);


--
-- Name: video_segment_versions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_segment_versions ALTER COLUMN id SET DEFAULT nextval('public.video_segment_versions_id_seq'::regclass);


--
-- Name: video_segments id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_segments ALTER COLUMN id SET DEFAULT nextval('public.video_segments_id_seq'::regclass);


--
-- Name: video_shot_edit_records id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_shot_edit_records ALTER COLUMN id SET DEFAULT nextval('public.video_shot_edit_records_id_seq'::regclass);


--
-- Name: video_story_outline id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_story_outline ALTER COLUMN id SET DEFAULT nextval('public.video_story_outline_id_seq'::regclass);


--
-- Name: video_story_outline_versions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_story_outline_versions ALTER COLUMN id SET DEFAULT nextval('public.video_story_outline_versions_id_seq'::regclass);


--
-- Name: video_storyboard_details id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_storyboard_details ALTER COLUMN id SET DEFAULT nextval('public.video_storyboard_details_id_seq'::regclass);


--
-- Name: video_storyboard_edit_records id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_storyboard_edit_records ALTER COLUMN id SET DEFAULT nextval('public.video_storyboard_edit_records_id_seq'::regclass);


--
-- Name: video_task_records id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_task_records ALTER COLUMN id SET DEFAULT nextval('public.video_task_records_id_seq'::regclass);


--
-- Name: app_configs app_configs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.app_configs
    ADD CONSTRAINT app_configs_pkey PRIMARY KEY (id);


--
-- Name: apscheduler_jobs apscheduler_jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.apscheduler_jobs
    ADD CONSTRAINT apscheduler_jobs_pkey PRIMARY KEY (id);


--
-- Name: character_folders character_folders_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.character_folders
    ADD CONSTRAINT character_folders_pkey PRIMARY KEY (id);


--
-- Name: characters characters_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.characters
    ADD CONSTRAINT characters_pkey PRIMARY KEY (id);


--
-- Name: characters_v2 characters_v2_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.characters_v2
    ADD CONSTRAINT characters_v2_pkey PRIMARY KEY (id);


--
-- Name: characters_v2 characters_v2_uuid_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.characters_v2
    ADD CONSTRAINT characters_v2_uuid_key UNIQUE (uuid);


--
-- Name: checkpoint_blobs checkpoint_blobs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.checkpoint_blobs
    ADD CONSTRAINT checkpoint_blobs_pkey PRIMARY KEY (thread_id, checkpoint_ns, channel, version);


--
-- Name: checkpoint_migrations checkpoint_migrations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.checkpoint_migrations
    ADD CONSTRAINT checkpoint_migrations_pkey PRIMARY KEY (v);


--
-- Name: checkpoint_writes checkpoint_writes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.checkpoint_writes
    ADD CONSTRAINT checkpoint_writes_pkey PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx);


--
-- Name: checkpoints checkpoints_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.checkpoints
    ADD CONSTRAINT checkpoints_pkey PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id);


--
-- Name: conversation_messages conversation_messages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversation_messages
    ADD CONSTRAINT conversation_messages_pkey PRIMARY KEY (id);


--
-- Name: conversation_states conversation_states_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversation_states
    ADD CONSTRAINT conversation_states_pkey PRIMARY KEY (id);


--
-- Name: conversations conversations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversations
    ADD CONSTRAINT conversations_pkey PRIMARY KEY (id);


--
-- Name: creation_favorites creation_favorites_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.creation_favorites
    ADD CONSTRAINT creation_favorites_pkey PRIMARY KEY (id);


--
-- Name: creation_likes creation_likes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.creation_likes
    ADD CONSTRAINT creation_likes_pkey PRIMARY KEY (id);


--
-- Name: creations creations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.creations
    ADD CONSTRAINT creations_pkey PRIMARY KEY (id);


--
-- Name: creations creations_uuid_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.creations
    ADD CONSTRAINT creations_uuid_key UNIQUE (uuid);


--
-- Name: credit_allocations credit_allocations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.credit_allocations
    ADD CONSTRAINT credit_allocations_pkey PRIMARY KEY (id);


--
-- Name: credit_history credit_history_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.credit_history
    ADD CONSTRAINT credit_history_pkey PRIMARY KEY (id);


--
-- Name: curated_style_prompts curated_style_prompts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.curated_style_prompts
    ADD CONSTRAINT curated_style_prompts_pkey PRIMARY KEY (id);


--
-- Name: curated_style_prompts curated_style_prompts_uuid_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.curated_style_prompts
    ADD CONSTRAINT curated_style_prompts_uuid_key UNIQUE (uuid);


--
-- Name: email_verifications email_verifications_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.email_verifications
    ADD CONSTRAINT email_verifications_pkey PRIMARY KEY (id);


--
-- Name: invite_code_quotas invite_code_quotas_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invite_code_quotas
    ADD CONSTRAINT invite_code_quotas_pkey PRIMARY KEY (id);


--
-- Name: invite_code_quotas invite_code_quotas_user_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invite_code_quotas
    ADD CONSTRAINT invite_code_quotas_user_id_key UNIQUE (user_id);


--
-- Name: invite_codes invite_codes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invite_codes
    ADD CONSTRAINT invite_codes_pkey PRIMARY KEY (id);


--
-- Name: llm_api_logs llm_api_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_api_logs
    ADD CONSTRAINT llm_api_logs_pkey PRIMARY KEY (id);


--
-- Name: paymentpackage paymentpackage_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.paymentpackage
    ADD CONSTRAINT paymentpackage_pkey PRIMARY KEY (id);


--
-- Name: paymentsettings paymentsettings_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.paymentsettings
    ADD CONSTRAINT paymentsettings_key_key UNIQUE (key);


--
-- Name: paymentsettings paymentsettings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.paymentsettings
    ADD CONSTRAINT paymentsettings_pkey PRIMARY KEY (id);


--
-- Name: paymenttransaction paymenttransaction_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.paymenttransaction
    ADD CONSTRAINT paymenttransaction_pkey PRIMARY KEY (id);


--
-- Name: paymenttransaction paymenttransaction_transaction_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.paymenttransaction
    ADD CONSTRAINT paymenttransaction_transaction_id_key UNIQUE (transaction_id);


--
-- Name: share_links share_links_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.share_links
    ADD CONSTRAINT share_links_pkey PRIMARY KEY (id);


--
-- Name: stories stories_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.stories
    ADD CONSTRAINT stories_pkey PRIMARY KEY (id);


--
-- Name: storybook_styles storybook_styles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.storybook_styles
    ADD CONSTRAINT storybook_styles_pkey PRIMARY KEY (id);


--
-- Name: storybooks storybooks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.storybooks
    ADD CONSTRAINT storybooks_pkey PRIMARY KEY (id);


--
-- Name: subscription_plans subscription_plans_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subscription_plans
    ADD CONSTRAINT subscription_plans_pkey PRIMARY KEY (id);


--
-- Name: subscription_plans subscription_plans_stripe_price_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subscription_plans
    ADD CONSTRAINT subscription_plans_stripe_price_id_key UNIQUE (stripe_price_id);


--
-- Name: subscriptions subscriptions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subscriptions
    ADD CONSTRAINT subscriptions_pkey PRIMARY KEY (id);


--
-- Name: task_executions task_executions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_executions
    ADD CONSTRAINT task_executions_pkey PRIMARY KEY (id);


--
-- Name: user_credits user_credits_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_credits
    ADD CONSTRAINT user_credits_pkey PRIMARY KEY (id);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: video_analysis video_analysis_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_analysis
    ADD CONSTRAINT video_analysis_pkey PRIMARY KEY (id);


--
-- Name: video_assemblies video_assemblies_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_assemblies
    ADD CONSTRAINT video_assemblies_pkey PRIMARY KEY (id);


--
-- Name: video_audio_effect_versions video_audio_effect_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_audio_effect_versions
    ADD CONSTRAINT video_audio_effect_versions_pkey PRIMARY KEY (id);


--
-- Name: video_audio_effects video_audio_effects_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_audio_effects
    ADD CONSTRAINT video_audio_effects_pkey PRIMARY KEY (id);


--
-- Name: video_audio_section video_audio_section_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_audio_section
    ADD CONSTRAINT video_audio_section_pkey PRIMARY KEY (id);


--
-- Name: video_audio_section video_audio_section_uuid_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_audio_section
    ADD CONSTRAINT video_audio_section_uuid_key UNIQUE (uuid);


--
-- Name: video_audio_segment video_audio_segment_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_audio_segment
    ADD CONSTRAINT video_audio_segment_pkey PRIMARY KEY (id);


--
-- Name: video_audio_transcription video_audio_transcription_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_audio_transcription
    ADD CONSTRAINT video_audio_transcription_pkey PRIMARY KEY (id);


--
-- Name: video_chapter_versions video_chapter_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_chapter_versions
    ADD CONSTRAINT video_chapter_versions_pkey PRIMARY KEY (id);


--
-- Name: video_chapter_versions video_chapter_versions_uuid_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_chapter_versions
    ADD CONSTRAINT video_chapter_versions_uuid_key UNIQUE (uuid);


--
-- Name: video_chapters video_chapters_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_chapters
    ADD CONSTRAINT video_chapters_pkey PRIMARY KEY (id);


--
-- Name: video_character_edit_records video_character_edit_records_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_character_edit_records
    ADD CONSTRAINT video_character_edit_records_pkey PRIMARY KEY (id);


--
-- Name: video_character_fusion_images video_character_fusion_images_fusion_key_user_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_character_fusion_images
    ADD CONSTRAINT video_character_fusion_images_fusion_key_user_id_key UNIQUE (fusion_key, user_id);


--
-- Name: video_character_fusion_images video_character_fusion_images_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_character_fusion_images
    ADD CONSTRAINT video_character_fusion_images_pkey PRIMARY KEY (id);


--
-- Name: video_character_fusion_images video_character_fusion_images_uuid_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_character_fusion_images
    ADD CONSTRAINT video_character_fusion_images_uuid_key UNIQUE (uuid);


--
-- Name: video_character_generation_versions video_character_generation_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_character_generation_versions
    ADD CONSTRAINT video_character_generation_versions_pkey PRIMARY KEY (id);


--
-- Name: video_character_multi_view_image_versions video_character_multi_view_image_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_character_multi_view_image_versions
    ADD CONSTRAINT video_character_multi_view_image_versions_pkey PRIMARY KEY (id);


--
-- Name: video_character_multi_view_image_versions video_character_multi_view_image_versions_uuid_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_character_multi_view_image_versions
    ADD CONSTRAINT video_character_multi_view_image_versions_uuid_key UNIQUE (uuid);


--
-- Name: video_character_multi_view_images video_character_multi_view_images_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_character_multi_view_images
    ADD CONSTRAINT video_character_multi_view_images_pkey PRIMARY KEY (id);


--
-- Name: video_character_multi_view_images video_character_multi_view_images_uuid_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_character_multi_view_images
    ADD CONSTRAINT video_character_multi_view_images_uuid_key UNIQUE (uuid);


--
-- Name: video_characters video_characters_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_characters
    ADD CONSTRAINT video_characters_pkey PRIMARY KEY (id);


--
-- Name: video_detailed_shots video_detailed_shots_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_detailed_shots
    ADD CONSTRAINT video_detailed_shots_pkey PRIMARY KEY (id);


--
-- Name: video_generation_versions video_generation_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_generation_versions
    ADD CONSTRAINT video_generation_versions_pkey PRIMARY KEY (id);


--
-- Name: video_generations video_generations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_generations
    ADD CONSTRAINT video_generations_pkey PRIMARY KEY (id);


--
-- Name: video_keyframe_reflection_results video_keyframe_reflection_results_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_keyframe_reflection_results
    ADD CONSTRAINT video_keyframe_reflection_results_pkey PRIMARY KEY (id);


--
-- Name: video_keyframe_reflection_results video_keyframe_reflection_results_uuid_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_keyframe_reflection_results
    ADD CONSTRAINT video_keyframe_reflection_results_uuid_key UNIQUE (uuid);


--
-- Name: video_keyframe_reflections video_keyframe_reflections_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_keyframe_reflections
    ADD CONSTRAINT video_keyframe_reflections_pkey PRIMARY KEY (id);


--
-- Name: video_keyframe_reflections video_keyframe_reflections_uuid_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_keyframe_reflections
    ADD CONSTRAINT video_keyframe_reflections_uuid_key UNIQUE (uuid);


--
-- Name: video_keyframe_versions video_keyframe_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_keyframe_versions
    ADD CONSTRAINT video_keyframe_versions_pkey PRIMARY KEY (id);


--
-- Name: video_keyframes video_keyframes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_keyframes
    ADD CONSTRAINT video_keyframes_pkey PRIMARY KEY (id);


--
-- Name: video_lipsync_generation_versions video_lipsync_generation_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_lipsync_generation_versions
    ADD CONSTRAINT video_lipsync_generation_versions_pkey PRIMARY KEY (id);


--
-- Name: video_lipsync_generations video_lipsync_generations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_lipsync_generations
    ADD CONSTRAINT video_lipsync_generations_pkey PRIMARY KEY (id);


--
-- Name: video_music_generation_versions video_music_generation_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_music_generation_versions
    ADD CONSTRAINT video_music_generation_versions_pkey PRIMARY KEY (id);


--
-- Name: video_music_generation_versions video_music_generation_versions_uuid_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_music_generation_versions
    ADD CONSTRAINT video_music_generation_versions_uuid_key UNIQUE (uuid);


--
-- Name: video_music_generations video_music_generations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_music_generations
    ADD CONSTRAINT video_music_generations_pkey PRIMARY KEY (id);


--
-- Name: video_narration_versions video_narration_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_narration_versions
    ADD CONSTRAINT video_narration_versions_pkey PRIMARY KEY (id);


--
-- Name: video_narrations video_narrations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_narrations
    ADD CONSTRAINT video_narrations_pkey PRIMARY KEY (id);


--
-- Name: video_scene_versions video_scene_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_scene_versions
    ADD CONSTRAINT video_scene_versions_pkey PRIMARY KEY (id);


--
-- Name: video_scene_versions video_scene_versions_uuid_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_scene_versions
    ADD CONSTRAINT video_scene_versions_uuid_key UNIQUE (uuid);


--
-- Name: video_scenes video_scenes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_scenes
    ADD CONSTRAINT video_scenes_pkey PRIMARY KEY (id);


--
-- Name: video_segment_versions video_segment_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_segment_versions
    ADD CONSTRAINT video_segment_versions_pkey PRIMARY KEY (id);


--
-- Name: video_segments video_segments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_segments
    ADD CONSTRAINT video_segments_pkey PRIMARY KEY (id);


--
-- Name: video_shot_edit_records video_shot_edit_records_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_shot_edit_records
    ADD CONSTRAINT video_shot_edit_records_pkey PRIMARY KEY (id);


--
-- Name: video_story_outline video_story_outline_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_story_outline
    ADD CONSTRAINT video_story_outline_pkey PRIMARY KEY (id);


--
-- Name: video_story_outline_versions video_story_outline_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_story_outline_versions
    ADD CONSTRAINT video_story_outline_versions_pkey PRIMARY KEY (id);


--
-- Name: video_story_outline_versions video_story_outline_versions_uuid_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_story_outline_versions
    ADD CONSTRAINT video_story_outline_versions_uuid_key UNIQUE (uuid);


--
-- Name: video_storyboard_details video_storyboard_details_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_storyboard_details
    ADD CONSTRAINT video_storyboard_details_pkey PRIMARY KEY (id);


--
-- Name: video_storyboard_edit_records video_storyboard_edit_records_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_storyboard_edit_records
    ADD CONSTRAINT video_storyboard_edit_records_pkey PRIMARY KEY (id);


--
-- Name: video_task_records video_task_records_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_task_records
    ADD CONSTRAINT video_task_records_pkey PRIMARY KEY (id);


--
-- Name: checkpoint_blobs_thread_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX checkpoint_blobs_thread_id_idx ON public.checkpoint_blobs USING btree (thread_id);


--
-- Name: checkpoint_writes_thread_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX checkpoint_writes_thread_id_idx ON public.checkpoint_writes USING btree (thread_id);


--
-- Name: checkpoints_thread_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX checkpoints_thread_id_idx ON public.checkpoints USING btree (thread_id);


--
-- Name: conversation_runs_run_id_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX conversation_runs_run_id_key ON public.conversation_runs USING btree (run_id);


--
-- Name: conversation_runs_uuid_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX conversation_runs_uuid_key ON public.conversation_runs USING btree (uuid);


--
-- Name: idx_character_folders_display_order; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_character_folders_display_order ON public.character_folders USING btree (display_order);


--
-- Name: idx_character_folders_is_deleted; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_character_folders_is_deleted ON public.character_folders USING btree (is_deleted);


--
-- Name: idx_character_folders_parent_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_character_folders_parent_id ON public.character_folders USING btree (parent_id);


--
-- Name: idx_character_folders_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_character_folders_user_id ON public.character_folders USING btree (user_id);


--
-- Name: idx_characters_v2_folder_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_characters_v2_folder_id ON public.characters_v2 USING btree (folder_id);


--
-- Name: idx_characters_v2_is_deleted; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_characters_v2_is_deleted ON public.characters_v2 USING btree (is_deleted);


--
-- Name: idx_characters_v2_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_characters_v2_user_id ON public.characters_v2 USING btree (user_id);


--
-- Name: idx_characters_v2_uuid; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_characters_v2_uuid ON public.characters_v2 USING btree (uuid);


--
-- Name: idx_conversation_runs_conversation_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_conversation_runs_conversation_id ON public.conversation_runs USING btree (conversation_id);


--
-- Name: idx_conversation_runs_conversation_uuid; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_conversation_runs_conversation_uuid ON public.conversation_runs USING btree (conversation_uuid);


--
-- Name: idx_conversation_runs_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_conversation_runs_run_id ON public.conversation_runs USING btree (run_id);


--
-- Name: idx_conversation_runs_thread_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_conversation_runs_thread_id ON public.conversation_runs USING btree (thread_id);


--
-- Name: idx_conversation_runs_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_conversation_runs_user_id ON public.conversation_runs USING btree (user_id);


--
-- Name: idx_conversation_runs_uuid; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_conversation_runs_uuid ON public.conversation_runs USING btree (uuid);


--
-- Name: idx_creation_favorites_user_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_creation_favorites_user_created ON public.creation_favorites USING btree (user_id, created_at DESC);


--
-- Name: idx_creation_likes_creation; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_creation_likes_creation ON public.creation_likes USING btree (creation_id);


--
-- Name: idx_creations_published_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_creations_published_at ON public.creations USING btree (published_at DESC) WHERE ((status)::text = 'published'::text);


--
-- Name: idx_creations_status_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_creations_status_created ON public.creations USING btree (status, created_at DESC);


--
-- Name: idx_creations_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_creations_user_id ON public.creations USING btree (user_id);


--
-- Name: idx_credit_allocations_expires_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_credit_allocations_expires_at ON public.credit_allocations USING btree (expires_at);


--
-- Name: idx_credit_allocations_is_expired; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_credit_allocations_is_expired ON public.credit_allocations USING btree (is_expired);


--
-- Name: idx_credit_allocations_stripe_invoice_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_credit_allocations_stripe_invoice_id ON public.credit_allocations USING btree (stripe_invoice_id);


--
-- Name: idx_credit_allocations_subscription_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_credit_allocations_subscription_id ON public.credit_allocations USING btree (subscription_id);


--
-- Name: idx_credit_allocations_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_credit_allocations_user_id ON public.credit_allocations USING btree (user_id);


--
-- Name: idx_curated_style_prompts_category; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_curated_style_prompts_category ON public.curated_style_prompts USING btree (category);


--
-- Name: idx_curated_style_prompts_uuid; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_curated_style_prompts_uuid ON public.curated_style_prompts USING btree (uuid);


--
-- Name: idx_invite_code_quotas_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_invite_code_quotas_user_id ON public.invite_code_quotas USING btree (user_id);


--
-- Name: idx_share_links_created_by_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_share_links_created_by_time ON public.share_links USING btree (created_by, created_at DESC);


--
-- Name: idx_subscription_plans_is_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_subscription_plans_is_active ON public.subscription_plans USING btree (is_active);


--
-- Name: idx_subscription_plans_monthly_credits; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_subscription_plans_monthly_credits ON public.subscription_plans USING btree (monthly_credits);


--
-- Name: idx_subscription_plans_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_subscription_plans_name ON public.subscription_plans USING btree (name);


--
-- Name: idx_subscription_plans_stripe_price_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_subscription_plans_stripe_price_id ON public.subscription_plans USING btree (stripe_price_id);


--
-- Name: idx_subscriptions_is_deleted; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_subscriptions_is_deleted ON public.subscriptions USING btree (is_deleted);


--
-- Name: idx_subscriptions_plan_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_subscriptions_plan_id ON public.subscriptions USING btree (plan_id);


--
-- Name: idx_subscriptions_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_subscriptions_status ON public.subscriptions USING btree (status);


--
-- Name: idx_subscriptions_stripe_customer_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_subscriptions_stripe_customer_id ON public.subscriptions USING btree (stripe_customer_id);


--
-- Name: idx_subscriptions_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_subscriptions_user_id ON public.subscriptions USING btree (user_id);


--
-- Name: idx_video_assemblies_error; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_video_assemblies_error ON public.video_assemblies USING btree (success) WHERE (success = false);


--
-- Name: idx_video_audio_effect_versions_error; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_video_audio_effect_versions_error ON public.video_audio_effect_versions USING btree (success) WHERE (success = false);


--
-- Name: idx_video_audio_section_transcription; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_video_audio_section_transcription ON public.video_audio_section USING btree (transcription_uuid);


--
-- Name: idx_video_audio_section_transcription_start; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_video_audio_section_transcription_start ON public.video_audio_section USING btree (transcription_uuid, start_time);


--
-- Name: idx_video_chapter_versions_chapter_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_video_chapter_versions_chapter_id ON public.video_chapter_versions USING btree (chapter_id);


--
-- Name: idx_video_chapters_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_video_chapters_user_id ON public.video_chapters USING btree (user_id);


--
-- Name: idx_video_character_generation_versions_error; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_video_character_generation_versions_error ON public.video_character_generation_versions USING btree (success) WHERE (success = false);


--
-- Name: idx_video_characters_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_video_characters_type ON public.video_characters USING btree (type);


--
-- Name: idx_video_characters_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_video_characters_user_id ON public.video_characters USING btree (user_id);


--
-- Name: idx_video_generation_versions_error; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_video_generation_versions_error ON public.video_generation_versions USING btree (success) WHERE (success = false);


--
-- Name: idx_video_keyframe_versions_error; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_video_keyframe_versions_error ON public.video_keyframe_versions USING btree (success) WHERE (success = false);


--
-- Name: idx_video_lipsync_generation_versions_error; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_video_lipsync_generation_versions_error ON public.video_lipsync_generation_versions USING btree (success) WHERE (success = false);


--
-- Name: idx_video_music_generation_versions_audio_transcription_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_video_music_generation_versions_audio_transcription_id ON public.video_music_generation_versions USING btree (audio_transcription_id);


--
-- Name: idx_video_music_generation_versions_conversation_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_video_music_generation_versions_conversation_id ON public.video_music_generation_versions USING btree (conversation_id);


--
-- Name: idx_video_music_generation_versions_error; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_video_music_generation_versions_error ON public.video_music_generation_versions USING btree (success) WHERE (success = false);


--
-- Name: idx_video_music_generation_versions_music_generation_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_video_music_generation_versions_music_generation_id ON public.video_music_generation_versions USING btree (music_generation_id);


--
-- Name: idx_video_music_generation_versions_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_video_music_generation_versions_run_id ON public.video_music_generation_versions USING btree (run_id);


--
-- Name: idx_video_music_generation_versions_thread_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_video_music_generation_versions_thread_id ON public.video_music_generation_versions USING btree (thread_id);


--
-- Name: idx_video_music_generation_versions_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_video_music_generation_versions_user_id ON public.video_music_generation_versions USING btree (user_id);


--
-- Name: idx_video_music_generations_scene_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_video_music_generations_scene_id ON public.video_music_generations USING btree (scene_id);


--
-- Name: idx_video_narration_versions_error; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_video_narration_versions_error ON public.video_narration_versions USING btree (success) WHERE (success = false);


--
-- Name: idx_video_scene_versions_scene_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_video_scene_versions_scene_id ON public.video_scene_versions USING btree (scene_id);


--
-- Name: idx_video_scenes_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_video_scenes_user_id ON public.video_scenes USING btree (user_id);


--
-- Name: idx_video_segment_versions_error; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_video_segment_versions_error ON public.video_segment_versions USING btree (success) WHERE (success = false);


--
-- Name: idx_video_story_outline_analysis_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_video_story_outline_analysis_id ON public.video_story_outline USING btree (analysis_id);


--
-- Name: idx_video_story_outline_versions_outline_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_video_story_outline_versions_outline_id ON public.video_story_outline_versions USING btree (story_outline_id);


--
-- Name: ix_app_configs_category; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_app_configs_category ON public.app_configs USING btree (category);


--
-- Name: ix_app_configs_is_system; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_app_configs_is_system ON public.app_configs USING btree (is_system);


--
-- Name: ix_app_configs_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_app_configs_key ON public.app_configs USING btree (key);


--
-- Name: ix_apscheduler_jobs_next_run_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_apscheduler_jobs_next_run_time ON public.apscheduler_jobs USING btree (next_run_time);


--
-- Name: ix_characters_display_order; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_characters_display_order ON public.characters USING btree (display_order);


--
-- Name: ix_characters_is_deleted; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_characters_is_deleted ON public.characters USING btree (is_deleted);


--
-- Name: ix_characters_storybook_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_characters_storybook_id ON public.characters USING btree (storybook_id);


--
-- Name: ix_characters_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_characters_user_id ON public.characters USING btree (user_id);


--
-- Name: ix_conversation_messages_conversation_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_conversation_messages_conversation_id ON public.conversation_messages USING btree (conversation_id);


--
-- Name: ix_conversation_messages_conversation_uuid; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_conversation_messages_conversation_uuid ON public.conversation_messages USING btree (conversation_uuid);


--
-- Name: ix_conversation_messages_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_conversation_messages_run_id ON public.conversation_messages USING btree (run_id);


--
-- Name: ix_conversation_messages_source; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_conversation_messages_source ON public.conversation_messages USING btree (source);


--
-- Name: ix_conversation_messages_uuid; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_conversation_messages_uuid ON public.conversation_messages USING btree (uuid);


--
-- Name: ix_conversation_states_conversation_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_conversation_states_conversation_id ON public.conversation_states USING btree (conversation_id);


--
-- Name: ix_conversation_states_thread_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_conversation_states_thread_id ON public.conversation_states USING btree (thread_id);


--
-- Name: ix_conversations_thread_id; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_conversations_thread_id ON public.conversations USING btree (thread_id);


--
-- Name: ix_conversations_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_conversations_user_id ON public.conversations USING btree (user_id);


--
-- Name: ix_conversations_uuid; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_conversations_uuid ON public.conversations USING btree (uuid);


--
-- Name: ix_credit_history_is_deleted; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_credit_history_is_deleted ON public.credit_history USING btree (is_deleted);


--
-- Name: ix_credit_history_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_credit_history_user_id ON public.credit_history USING btree (user_id);


--
-- Name: ix_email_verifications_email; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_email_verifications_email ON public.email_verifications USING btree (email);


--
-- Name: ix_email_verifications_expires_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_email_verifications_expires_at ON public.email_verifications USING btree (expires_at);


--
-- Name: ix_email_verifications_is_deleted; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_email_verifications_is_deleted ON public.email_verifications USING btree (is_deleted);


--
-- Name: ix_invite_codes_code; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_invite_codes_code ON public.invite_codes USING btree (code);


--
-- Name: ix_invite_codes_creator_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_invite_codes_creator_id ON public.invite_codes USING btree (creator_id);


--
-- Name: ix_invite_codes_is_deleted; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_invite_codes_is_deleted ON public.invite_codes USING btree (is_deleted);


--
-- Name: ix_invite_codes_used_by_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_invite_codes_used_by_id ON public.invite_codes USING btree (used_by_id);


--
-- Name: ix_llm_api_logs_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_llm_api_logs_created_at ON public.llm_api_logs USING btree (created_at);


--
-- Name: ix_llm_api_logs_is_deleted; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_llm_api_logs_is_deleted ON public.llm_api_logs USING btree (is_deleted);


--
-- Name: ix_llm_api_logs_method; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_llm_api_logs_method ON public.llm_api_logs USING btree (method);


--
-- Name: ix_llm_api_logs_model; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_llm_api_logs_model ON public.llm_api_logs USING btree (model);


--
-- Name: ix_llm_api_logs_provider_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_llm_api_logs_provider_type ON public.llm_api_logs USING btree (provider_type);


--
-- Name: ix_llm_api_logs_storybook_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_llm_api_logs_storybook_id ON public.llm_api_logs USING btree (storybook_id);


--
-- Name: ix_llm_api_logs_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_llm_api_logs_user_id ON public.llm_api_logs USING btree (user_id);


--
-- Name: ix_paymentpackage_is_deleted; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_paymentpackage_is_deleted ON public.paymentpackage USING btree (is_deleted);


--
-- Name: ix_paymentsettings_is_deleted; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_paymentsettings_is_deleted ON public.paymentsettings USING btree (is_deleted);


--
-- Name: ix_paymenttransaction_package_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_paymenttransaction_package_id ON public.paymenttransaction USING btree (package_id);


--
-- Name: ix_paymenttransaction_stripe_checkout_session_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_paymenttransaction_stripe_checkout_session_id ON public.paymenttransaction USING btree (stripe_checkout_session_id);


--
-- Name: ix_paymenttransaction_stripe_payment_intent_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_paymenttransaction_stripe_payment_intent_id ON public.paymenttransaction USING btree (stripe_payment_intent_id);


--
-- Name: ix_paymenttransaction_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_paymenttransaction_user_id ON public.paymenttransaction USING btree (user_id);


--
-- Name: ix_stories_is_deleted; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_stories_is_deleted ON public.stories USING btree (is_deleted);


--
-- Name: ix_stories_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_stories_user_id ON public.stories USING btree (user_id);


--
-- Name: ix_storybook_styles_display_order; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_storybook_styles_display_order ON public.storybook_styles USING btree (display_order);


--
-- Name: ix_storybook_styles_is_deleted; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_storybook_styles_is_deleted ON public.storybook_styles USING btree (is_deleted);


--
-- Name: ix_storybook_styles_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_storybook_styles_user_id ON public.storybook_styles USING btree (user_id);


--
-- Name: ix_storybooks_character_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_storybooks_character_id ON public.storybooks USING btree (character_id);


--
-- Name: ix_storybooks_is_deleted; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_storybooks_is_deleted ON public.storybooks USING btree (is_deleted);


--
-- Name: ix_storybooks_story_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_storybooks_story_id ON public.storybooks USING btree (story_id);


--
-- Name: ix_storybooks_style_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_storybooks_style_id ON public.storybooks USING btree (style_id);


--
-- Name: ix_storybooks_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_storybooks_user_id ON public.storybooks USING btree (user_id);


--
-- Name: ix_subscriptions_order_id; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_subscriptions_order_id ON public.subscriptions USING btree (order_id);


--
-- Name: ix_task_executions_task_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_task_executions_task_id ON public.task_executions USING btree (task_id);


--
-- Name: ix_user_credits_is_deleted; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_user_credits_is_deleted ON public.user_credits USING btree (is_deleted);


--
-- Name: ix_user_credits_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_user_credits_user_id ON public.user_credits USING btree (user_id);


--
-- Name: ix_users_email; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_users_email ON public.users USING btree (email);


--
-- Name: ix_users_is_deleted; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_users_is_deleted ON public.users USING btree (is_deleted);


--
-- Name: ix_users_phone; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_users_phone ON public.users USING btree (phone);


--
-- Name: ix_users_stripe_customer_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_users_stripe_customer_id ON public.users USING btree (stripe_customer_id);


--
-- Name: ix_users_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_users_user_id ON public.users USING btree (user_id);


--
-- Name: ix_users_username; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_users_username ON public.users USING btree (username);


--
-- Name: ix_video_analysis_conversation_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_analysis_conversation_id ON public.video_analysis USING btree (conversation_id);


--
-- Name: ix_video_analysis_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_analysis_run_id ON public.video_analysis USING btree (run_id);


--
-- Name: ix_video_analysis_thread_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_analysis_thread_id ON public.video_analysis USING btree (thread_id);


--
-- Name: ix_video_analysis_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_analysis_user_id ON public.video_analysis USING btree (user_id);


--
-- Name: ix_video_analysis_uuid; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_video_analysis_uuid ON public.video_analysis USING btree (uuid);


--
-- Name: ix_video_assemblies_conversation_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_assemblies_conversation_id ON public.video_assemblies USING btree (conversation_id);


--
-- Name: ix_video_assemblies_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_assemblies_run_id ON public.video_assemblies USING btree (run_id);


--
-- Name: ix_video_assemblies_story_outline_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_assemblies_story_outline_id ON public.video_assemblies USING btree (story_outline_id);


--
-- Name: ix_video_assemblies_thread_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_assemblies_thread_id ON public.video_assemblies USING btree (thread_id);


--
-- Name: ix_video_assemblies_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_assemblies_user_id ON public.video_assemblies USING btree (user_id);


--
-- Name: ix_video_assemblies_uuid; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_video_assemblies_uuid ON public.video_assemblies USING btree (uuid);


--
-- Name: ix_video_audio_effect_versions_audio_effect_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_audio_effect_versions_audio_effect_id ON public.video_audio_effect_versions USING btree (audio_effect_id);


--
-- Name: ix_video_audio_effect_versions_conversation_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_audio_effect_versions_conversation_id ON public.video_audio_effect_versions USING btree (conversation_id);


--
-- Name: ix_video_audio_effect_versions_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_audio_effect_versions_run_id ON public.video_audio_effect_versions USING btree (run_id);


--
-- Name: ix_video_audio_effect_versions_thread_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_audio_effect_versions_thread_id ON public.video_audio_effect_versions USING btree (thread_id);


--
-- Name: ix_video_audio_effect_versions_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_audio_effect_versions_user_id ON public.video_audio_effect_versions USING btree (user_id);


--
-- Name: ix_video_audio_effect_versions_uuid; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_video_audio_effect_versions_uuid ON public.video_audio_effect_versions USING btree (uuid);


--
-- Name: ix_video_audio_effects_conversation_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_audio_effects_conversation_id ON public.video_audio_effects USING btree (conversation_id);


--
-- Name: ix_video_audio_effects_detailed_shot_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_audio_effects_detailed_shot_id ON public.video_audio_effects USING btree (detailed_shot_id);


--
-- Name: ix_video_audio_effects_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_audio_effects_run_id ON public.video_audio_effects USING btree (run_id);


--
-- Name: ix_video_audio_effects_scene_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_audio_effects_scene_id ON public.video_audio_effects USING btree (scene_id);


--
-- Name: ix_video_audio_effects_story_outline_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_audio_effects_story_outline_id ON public.video_audio_effects USING btree (story_outline_id);


--
-- Name: ix_video_audio_effects_storyboard_detail_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_audio_effects_storyboard_detail_id ON public.video_audio_effects USING btree (storyboard_detail_id);


--
-- Name: ix_video_audio_effects_thread_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_audio_effects_thread_id ON public.video_audio_effects USING btree (thread_id);


--
-- Name: ix_video_audio_effects_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_audio_effects_user_id ON public.video_audio_effects USING btree (user_id);


--
-- Name: ix_video_audio_effects_uuid; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_video_audio_effects_uuid ON public.video_audio_effects USING btree (uuid);


--
-- Name: ix_video_audio_effects_video_generation_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_audio_effects_video_generation_id ON public.video_audio_effects USING btree (video_generation_id);


--
-- Name: ix_video_audio_segment_conversation_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_audio_segment_conversation_id ON public.video_audio_segment USING btree (conversation_id);


--
-- Name: ix_video_audio_segment_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_audio_segment_run_id ON public.video_audio_segment USING btree (run_id);


--
-- Name: ix_video_audio_segment_thread_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_audio_segment_thread_id ON public.video_audio_segment USING btree (thread_id);


--
-- Name: ix_video_audio_segment_transcription_uuid; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_audio_segment_transcription_uuid ON public.video_audio_segment USING btree (transcription_uuid);


--
-- Name: ix_video_audio_segment_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_audio_segment_user_id ON public.video_audio_segment USING btree (user_id);


--
-- Name: ix_video_audio_segment_uuid; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_video_audio_segment_uuid ON public.video_audio_segment USING btree (uuid);


--
-- Name: ix_video_audio_transcription_conversation_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_audio_transcription_conversation_id ON public.video_audio_transcription USING btree (conversation_id);


--
-- Name: ix_video_audio_transcription_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_audio_transcription_run_id ON public.video_audio_transcription USING btree (run_id);


--
-- Name: ix_video_audio_transcription_thread_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_audio_transcription_thread_id ON public.video_audio_transcription USING btree (thread_id);


--
-- Name: ix_video_audio_transcription_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_audio_transcription_user_id ON public.video_audio_transcription USING btree (user_id);


--
-- Name: ix_video_audio_transcription_uuid; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_video_audio_transcription_uuid ON public.video_audio_transcription USING btree (uuid);


--
-- Name: ix_video_chapters_conversation_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_chapters_conversation_id ON public.video_chapters USING btree (conversation_id);


--
-- Name: ix_video_chapters_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_chapters_run_id ON public.video_chapters USING btree (run_id);


--
-- Name: ix_video_chapters_story_outline_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_chapters_story_outline_id ON public.video_chapters USING btree (story_outline_id);


--
-- Name: ix_video_chapters_thread_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_chapters_thread_id ON public.video_chapters USING btree (thread_id);


--
-- Name: ix_video_chapters_uuid; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_video_chapters_uuid ON public.video_chapters USING btree (uuid);


--
-- Name: ix_video_character_edit_records_character_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_character_edit_records_character_id ON public.video_character_edit_records USING btree (character_id);


--
-- Name: ix_video_character_edit_records_conversation_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_character_edit_records_conversation_id ON public.video_character_edit_records USING btree (conversation_id);


--
-- Name: ix_video_character_edit_records_new_version_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_character_edit_records_new_version_id ON public.video_character_edit_records USING btree (new_version_id);


--
-- Name: ix_video_character_edit_records_old_version_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_character_edit_records_old_version_id ON public.video_character_edit_records USING btree (old_version_id);


--
-- Name: ix_video_character_edit_records_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_character_edit_records_run_id ON public.video_character_edit_records USING btree (run_id);


--
-- Name: ix_video_character_edit_records_task_record_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_character_edit_records_task_record_id ON public.video_character_edit_records USING btree (task_record_id);


--
-- Name: ix_video_character_edit_records_thread_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_character_edit_records_thread_id ON public.video_character_edit_records USING btree (thread_id);


--
-- Name: ix_video_character_edit_records_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_character_edit_records_user_id ON public.video_character_edit_records USING btree (user_id);


--
-- Name: ix_video_character_edit_records_uuid; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_video_character_edit_records_uuid ON public.video_character_edit_records USING btree (uuid);


--
-- Name: ix_video_character_generation_versions_conversation_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_character_generation_versions_conversation_id ON public.video_character_generation_versions USING btree (conversation_id);


--
-- Name: ix_video_character_generation_versions_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_character_generation_versions_run_id ON public.video_character_generation_versions USING btree (run_id);


--
-- Name: ix_video_character_generation_versions_thread_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_character_generation_versions_thread_id ON public.video_character_generation_versions USING btree (thread_id);


--
-- Name: ix_video_character_generation_versions_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_character_generation_versions_user_id ON public.video_character_generation_versions USING btree (user_id);


--
-- Name: ix_video_character_generation_versions_uuid; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_video_character_generation_versions_uuid ON public.video_character_generation_versions USING btree (uuid);


--
-- Name: ix_video_character_generation_versions_video_character_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_character_generation_versions_video_character_id ON public.video_character_generation_versions USING btree (video_character_id);


--
-- Name: ix_video_character_multi_view_images_character; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_character_multi_view_images_character ON public.video_character_multi_view_images USING btree (video_character_id, user_id);


--
-- Name: ix_video_character_mv_versions_char; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_character_mv_versions_char ON public.video_character_multi_view_image_versions USING btree (video_character_id);


--
-- Name: ix_video_characters_conversation_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_characters_conversation_id ON public.video_characters USING btree (conversation_id);


--
-- Name: ix_video_characters_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_characters_run_id ON public.video_characters USING btree (run_id);


--
-- Name: ix_video_characters_thread_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_characters_thread_id ON public.video_characters USING btree (thread_id);


--
-- Name: ix_video_characters_uuid; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_video_characters_uuid ON public.video_characters USING btree (uuid);


--
-- Name: ix_video_detailed_shots_chapter_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_detailed_shots_chapter_id ON public.video_detailed_shots USING btree (chapter_id);


--
-- Name: ix_video_detailed_shots_conversation_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_detailed_shots_conversation_id ON public.video_detailed_shots USING btree (conversation_id);


--
-- Name: ix_video_detailed_shots_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_detailed_shots_run_id ON public.video_detailed_shots USING btree (run_id);


--
-- Name: ix_video_detailed_shots_scene_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_detailed_shots_scene_id ON public.video_detailed_shots USING btree (scene_id);


--
-- Name: ix_video_detailed_shots_storyboard_detail_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_detailed_shots_storyboard_detail_id ON public.video_detailed_shots USING btree (storyboard_detail_id);


--
-- Name: ix_video_detailed_shots_thread_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_detailed_shots_thread_id ON public.video_detailed_shots USING btree (thread_id);


--
-- Name: ix_video_detailed_shots_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_detailed_shots_user_id ON public.video_detailed_shots USING btree (user_id);


--
-- Name: ix_video_detailed_shots_uuid; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_video_detailed_shots_uuid ON public.video_detailed_shots USING btree (uuid);


--
-- Name: ix_video_generation_versions_conversation_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_generation_versions_conversation_id ON public.video_generation_versions USING btree (conversation_id);


--
-- Name: ix_video_generation_versions_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_generation_versions_run_id ON public.video_generation_versions USING btree (run_id);


--
-- Name: ix_video_generation_versions_thread_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_generation_versions_thread_id ON public.video_generation_versions USING btree (thread_id);


--
-- Name: ix_video_generation_versions_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_generation_versions_user_id ON public.video_generation_versions USING btree (user_id);


--
-- Name: ix_video_generation_versions_uuid; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_video_generation_versions_uuid ON public.video_generation_versions USING btree (uuid);


--
-- Name: ix_video_generation_versions_video_generation_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_generation_versions_video_generation_id ON public.video_generation_versions USING btree (video_generation_id);


--
-- Name: ix_video_generations_conversation_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_generations_conversation_id ON public.video_generations USING btree (conversation_id);


--
-- Name: ix_video_generations_detailed_shot_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_generations_detailed_shot_id ON public.video_generations USING btree (detailed_shot_id);


--
-- Name: ix_video_generations_keyframe_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_generations_keyframe_id ON public.video_generations USING btree (keyframe_id);


--
-- Name: ix_video_generations_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_generations_run_id ON public.video_generations USING btree (run_id);


--
-- Name: ix_video_generations_scene_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_generations_scene_id ON public.video_generations USING btree (scene_id);


--
-- Name: ix_video_generations_story_outline_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_generations_story_outline_id ON public.video_generations USING btree (story_outline_id);


--
-- Name: ix_video_generations_storyboard_detail_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_generations_storyboard_detail_id ON public.video_generations USING btree (storyboard_detail_id);


--
-- Name: ix_video_generations_thread_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_generations_thread_id ON public.video_generations USING btree (thread_id);


--
-- Name: ix_video_generations_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_generations_user_id ON public.video_generations USING btree (user_id);


--
-- Name: ix_video_generations_uuid; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_video_generations_uuid ON public.video_generations USING btree (uuid);


--
-- Name: ix_video_keyframe_reflection_results_kv; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_keyframe_reflection_results_kv ON public.video_keyframe_reflection_results USING btree (keyframe_version_id);


--
-- Name: ix_video_keyframe_reflections_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_keyframe_reflections_run_id ON public.video_keyframe_reflections USING btree (run_id);


--
-- Name: ix_video_keyframe_versions_conversation_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_keyframe_versions_conversation_id ON public.video_keyframe_versions USING btree (conversation_id);


--
-- Name: ix_video_keyframe_versions_keyframe_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_keyframe_versions_keyframe_id ON public.video_keyframe_versions USING btree (keyframe_id);


--
-- Name: ix_video_keyframe_versions_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_keyframe_versions_run_id ON public.video_keyframe_versions USING btree (run_id);


--
-- Name: ix_video_keyframe_versions_thread_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_keyframe_versions_thread_id ON public.video_keyframe_versions USING btree (thread_id);


--
-- Name: ix_video_keyframe_versions_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_keyframe_versions_user_id ON public.video_keyframe_versions USING btree (user_id);


--
-- Name: ix_video_keyframe_versions_uuid; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_video_keyframe_versions_uuid ON public.video_keyframe_versions USING btree (uuid);


--
-- Name: ix_video_keyframes_conversation_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_keyframes_conversation_id ON public.video_keyframes USING btree (conversation_id);


--
-- Name: ix_video_keyframes_detailed_shot_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_keyframes_detailed_shot_id ON public.video_keyframes USING btree (detailed_shot_id);


--
-- Name: ix_video_keyframes_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_keyframes_run_id ON public.video_keyframes USING btree (run_id);


--
-- Name: ix_video_keyframes_scene_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_keyframes_scene_id ON public.video_keyframes USING btree (scene_id);


--
-- Name: ix_video_keyframes_story_outline_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_keyframes_story_outline_id ON public.video_keyframes USING btree (story_outline_id);


--
-- Name: ix_video_keyframes_storyboard_detail_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_keyframes_storyboard_detail_id ON public.video_keyframes USING btree (storyboard_detail_id);


--
-- Name: ix_video_keyframes_thread_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_keyframes_thread_id ON public.video_keyframes USING btree (thread_id);


--
-- Name: ix_video_keyframes_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_keyframes_user_id ON public.video_keyframes USING btree (user_id);


--
-- Name: ix_video_keyframes_uuid; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_video_keyframes_uuid ON public.video_keyframes USING btree (uuid);


--
-- Name: ix_video_lipsync_generation_versions_conversation_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_lipsync_generation_versions_conversation_id ON public.video_lipsync_generation_versions USING btree (conversation_id);


--
-- Name: ix_video_lipsync_generation_versions_lipsync_generation_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_lipsync_generation_versions_lipsync_generation_id ON public.video_lipsync_generation_versions USING btree (lipsync_generation_id);


--
-- Name: ix_video_lipsync_generation_versions_music_generation_v_4cbb; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_lipsync_generation_versions_music_generation_v_4cbb ON public.video_lipsync_generation_versions USING btree (music_generation_version_id);


--
-- Name: ix_video_lipsync_generation_versions_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_lipsync_generation_versions_run_id ON public.video_lipsync_generation_versions USING btree (run_id);


--
-- Name: ix_video_lipsync_generation_versions_thread_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_lipsync_generation_versions_thread_id ON public.video_lipsync_generation_versions USING btree (thread_id);


--
-- Name: ix_video_lipsync_generation_versions_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_lipsync_generation_versions_user_id ON public.video_lipsync_generation_versions USING btree (user_id);


--
-- Name: ix_video_lipsync_generation_versions_uuid; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_video_lipsync_generation_versions_uuid ON public.video_lipsync_generation_versions USING btree (uuid);


--
-- Name: ix_video_lipsync_generation_versions_video_segment_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_lipsync_generation_versions_video_segment_id ON public.video_lipsync_generation_versions USING btree (video_segment_id);


--
-- Name: ix_video_lipsync_generation_versions_video_segment_version_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_lipsync_generation_versions_video_segment_version_id ON public.video_lipsync_generation_versions USING btree (video_segment_version_id);


--
-- Name: ix_video_lipsync_generations_conversation_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_lipsync_generations_conversation_id ON public.video_lipsync_generations USING btree (conversation_id);


--
-- Name: ix_video_lipsync_generations_music_generation_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_lipsync_generations_music_generation_id ON public.video_lipsync_generations USING btree (music_generation_id);


--
-- Name: ix_video_lipsync_generations_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_lipsync_generations_run_id ON public.video_lipsync_generations USING btree (run_id);


--
-- Name: ix_video_lipsync_generations_thread_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_lipsync_generations_thread_id ON public.video_lipsync_generations USING btree (thread_id);


--
-- Name: ix_video_lipsync_generations_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_lipsync_generations_user_id ON public.video_lipsync_generations USING btree (user_id);


--
-- Name: ix_video_lipsync_generations_uuid; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_video_lipsync_generations_uuid ON public.video_lipsync_generations USING btree (uuid);


--
-- Name: ix_video_lipsync_generations_video_segment_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_lipsync_generations_video_segment_id ON public.video_lipsync_generations USING btree (video_segment_id);


--
-- Name: ix_video_music_generations_conversation_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_music_generations_conversation_id ON public.video_music_generations USING btree (conversation_id);


--
-- Name: ix_video_music_generations_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_music_generations_run_id ON public.video_music_generations USING btree (run_id);


--
-- Name: ix_video_music_generations_story_outline_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_music_generations_story_outline_id ON public.video_music_generations USING btree (story_outline_id);


--
-- Name: ix_video_music_generations_thread_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_music_generations_thread_id ON public.video_music_generations USING btree (thread_id);


--
-- Name: ix_video_music_generations_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_music_generations_user_id ON public.video_music_generations USING btree (user_id);


--
-- Name: ix_video_music_generations_uuid; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_video_music_generations_uuid ON public.video_music_generations USING btree (uuid);


--
-- Name: ix_video_narration_versions_conversation_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_narration_versions_conversation_id ON public.video_narration_versions USING btree (conversation_id);


--
-- Name: ix_video_narration_versions_narration_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_narration_versions_narration_id ON public.video_narration_versions USING btree (narration_id);


--
-- Name: ix_video_narration_versions_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_narration_versions_run_id ON public.video_narration_versions USING btree (run_id);


--
-- Name: ix_video_narration_versions_thread_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_narration_versions_thread_id ON public.video_narration_versions USING btree (thread_id);


--
-- Name: ix_video_narration_versions_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_narration_versions_user_id ON public.video_narration_versions USING btree (user_id);


--
-- Name: ix_video_narration_versions_uuid; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_video_narration_versions_uuid ON public.video_narration_versions USING btree (uuid);


--
-- Name: ix_video_narrations_conversation_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_narrations_conversation_id ON public.video_narrations USING btree (conversation_id);


--
-- Name: ix_video_narrations_detailed_shot_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_narrations_detailed_shot_id ON public.video_narrations USING btree (detailed_shot_id);


--
-- Name: ix_video_narrations_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_narrations_run_id ON public.video_narrations USING btree (run_id);


--
-- Name: ix_video_narrations_scene_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_narrations_scene_id ON public.video_narrations USING btree (scene_id);


--
-- Name: ix_video_narrations_story_outline_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_narrations_story_outline_id ON public.video_narrations USING btree (story_outline_id);


--
-- Name: ix_video_narrations_storyboard_detail_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_narrations_storyboard_detail_id ON public.video_narrations USING btree (storyboard_detail_id);


--
-- Name: ix_video_narrations_thread_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_narrations_thread_id ON public.video_narrations USING btree (thread_id);


--
-- Name: ix_video_narrations_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_narrations_user_id ON public.video_narrations USING btree (user_id);


--
-- Name: ix_video_narrations_uuid; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_video_narrations_uuid ON public.video_narrations USING btree (uuid);


--
-- Name: ix_video_scenes_conversation_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_scenes_conversation_id ON public.video_scenes USING btree (conversation_id);


--
-- Name: ix_video_scenes_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_scenes_run_id ON public.video_scenes USING btree (run_id);


--
-- Name: ix_video_scenes_thread_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_scenes_thread_id ON public.video_scenes USING btree (thread_id);


--
-- Name: ix_video_scenes_uuid; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_video_scenes_uuid ON public.video_scenes USING btree (uuid);


--
-- Name: ix_video_segment_versions_audio_effect_version_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_segment_versions_audio_effect_version_id ON public.video_segment_versions USING btree (audio_effect_version_id);


--
-- Name: ix_video_segment_versions_conversation_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_segment_versions_conversation_id ON public.video_segment_versions USING btree (conversation_id);


--
-- Name: ix_video_segment_versions_lipsync_version_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_segment_versions_lipsync_version_id ON public.video_segment_versions USING btree (lipsync_version_id);


--
-- Name: ix_video_segment_versions_music_generation_version_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_segment_versions_music_generation_version_id ON public.video_segment_versions USING btree (music_generation_version_id);


--
-- Name: ix_video_segment_versions_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_segment_versions_run_id ON public.video_segment_versions USING btree (run_id);


--
-- Name: ix_video_segment_versions_thread_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_segment_versions_thread_id ON public.video_segment_versions USING btree (thread_id);


--
-- Name: ix_video_segment_versions_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_segment_versions_user_id ON public.video_segment_versions USING btree (user_id);


--
-- Name: ix_video_segment_versions_uuid; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_video_segment_versions_uuid ON public.video_segment_versions USING btree (uuid);


--
-- Name: ix_video_segment_versions_video_segment_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_segment_versions_video_segment_id ON public.video_segment_versions USING btree (video_segment_id);


--
-- Name: ix_video_segments_audio_effect_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_segments_audio_effect_id ON public.video_segments USING btree (audio_effect_id);


--
-- Name: ix_video_segments_conversation_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_segments_conversation_id ON public.video_segments USING btree (conversation_id);


--
-- Name: ix_video_segments_lipsync_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_segments_lipsync_id ON public.video_segments USING btree (lipsync_id);


--
-- Name: ix_video_segments_music_generation_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_segments_music_generation_id ON public.video_segments USING btree (music_generation_id);


--
-- Name: ix_video_segments_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_segments_run_id ON public.video_segments USING btree (run_id);


--
-- Name: ix_video_segments_story_outline_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_segments_story_outline_id ON public.video_segments USING btree (story_outline_id);


--
-- Name: ix_video_segments_thread_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_segments_thread_id ON public.video_segments USING btree (thread_id);


--
-- Name: ix_video_segments_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_segments_user_id ON public.video_segments USING btree (user_id);


--
-- Name: ix_video_segments_uuid; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_video_segments_uuid ON public.video_segments USING btree (uuid);


--
-- Name: ix_video_shot_edit_records_cascaded_from_storyboard_edit_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_shot_edit_records_cascaded_from_storyboard_edit_id ON public.video_shot_edit_records USING btree (cascaded_from_storyboard_edit_id);


--
-- Name: ix_video_shot_edit_records_conversation_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_shot_edit_records_conversation_id ON public.video_shot_edit_records USING btree (conversation_id);


--
-- Name: ix_video_shot_edit_records_new_version_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_shot_edit_records_new_version_id ON public.video_shot_edit_records USING btree (new_version_id);


--
-- Name: ix_video_shot_edit_records_old_version_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_shot_edit_records_old_version_id ON public.video_shot_edit_records USING btree (old_version_id);


--
-- Name: ix_video_shot_edit_records_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_shot_edit_records_run_id ON public.video_shot_edit_records USING btree (run_id);


--
-- Name: ix_video_shot_edit_records_task_record_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_shot_edit_records_task_record_id ON public.video_shot_edit_records USING btree (task_record_id);


--
-- Name: ix_video_shot_edit_records_thread_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_shot_edit_records_thread_id ON public.video_shot_edit_records USING btree (thread_id);


--
-- Name: ix_video_shot_edit_records_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_shot_edit_records_user_id ON public.video_shot_edit_records USING btree (user_id);


--
-- Name: ix_video_shot_edit_records_uuid; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_video_shot_edit_records_uuid ON public.video_shot_edit_records USING btree (uuid);


--
-- Name: ix_video_shot_edit_records_video_generation_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_shot_edit_records_video_generation_id ON public.video_shot_edit_records USING btree (video_generation_id);


--
-- Name: ix_video_story_outline_conversation_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_story_outline_conversation_id ON public.video_story_outline USING btree (conversation_id);


--
-- Name: ix_video_story_outline_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_story_outline_run_id ON public.video_story_outline USING btree (run_id);


--
-- Name: ix_video_story_outline_thread_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_story_outline_thread_id ON public.video_story_outline USING btree (thread_id);


--
-- Name: ix_video_story_outline_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_story_outline_user_id ON public.video_story_outline USING btree (user_id);


--
-- Name: ix_video_story_outline_uuid; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_video_story_outline_uuid ON public.video_story_outline USING btree (uuid);


--
-- Name: ix_video_storyboard_details_conversation_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_storyboard_details_conversation_id ON public.video_storyboard_details USING btree (conversation_id);


--
-- Name: ix_video_storyboard_details_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_storyboard_details_run_id ON public.video_storyboard_details USING btree (run_id);


--
-- Name: ix_video_storyboard_details_story_outline_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_storyboard_details_story_outline_id ON public.video_storyboard_details USING btree (story_outline_id);


--
-- Name: ix_video_storyboard_details_thread_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_storyboard_details_thread_id ON public.video_storyboard_details USING btree (thread_id);


--
-- Name: ix_video_storyboard_details_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_storyboard_details_user_id ON public.video_storyboard_details USING btree (user_id);


--
-- Name: ix_video_storyboard_details_uuid; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_video_storyboard_details_uuid ON public.video_storyboard_details USING btree (uuid);


--
-- Name: ix_video_storyboard_edit_records_conversation_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_storyboard_edit_records_conversation_id ON public.video_storyboard_edit_records USING btree (conversation_id);


--
-- Name: ix_video_storyboard_edit_records_keyframe_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_storyboard_edit_records_keyframe_id ON public.video_storyboard_edit_records USING btree (keyframe_id);


--
-- Name: ix_video_storyboard_edit_records_new_version_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_storyboard_edit_records_new_version_id ON public.video_storyboard_edit_records USING btree (new_version_id);


--
-- Name: ix_video_storyboard_edit_records_old_version_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_storyboard_edit_records_old_version_id ON public.video_storyboard_edit_records USING btree (old_version_id);


--
-- Name: ix_video_storyboard_edit_records_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_storyboard_edit_records_run_id ON public.video_storyboard_edit_records USING btree (run_id);


--
-- Name: ix_video_storyboard_edit_records_task_record_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_storyboard_edit_records_task_record_id ON public.video_storyboard_edit_records USING btree (task_record_id);


--
-- Name: ix_video_storyboard_edit_records_thread_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_storyboard_edit_records_thread_id ON public.video_storyboard_edit_records USING btree (thread_id);


--
-- Name: ix_video_storyboard_edit_records_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_storyboard_edit_records_user_id ON public.video_storyboard_edit_records USING btree (user_id);


--
-- Name: ix_video_storyboard_edit_records_uuid; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_video_storyboard_edit_records_uuid ON public.video_storyboard_edit_records USING btree (uuid);


--
-- Name: ix_video_task_records_analysis_uuid; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_task_records_analysis_uuid ON public.video_task_records USING btree (analysis_uuid);


--
-- Name: ix_video_task_records_conversation_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_task_records_conversation_id ON public.video_task_records USING btree (conversation_id);


--
-- Name: ix_video_task_records_story_outline_uuid; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_task_records_story_outline_uuid ON public.video_task_records USING btree (story_outline_uuid);


--
-- Name: ix_video_task_records_task_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_task_records_task_id ON public.video_task_records USING btree (task_id);


--
-- Name: ix_video_task_records_thread_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_task_records_thread_id ON public.video_task_records USING btree (thread_id);


--
-- Name: ix_video_task_records_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_task_records_user_id ON public.video_task_records USING btree (user_id);


--
-- Name: ix_video_task_records_uuid; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_video_task_records_uuid ON public.video_task_records USING btree (uuid);


--
-- Name: ix_video_task_records_video_assembly_uuid; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_task_records_video_assembly_uuid ON public.video_task_records USING btree (video_assembly_uuid);


--
-- Name: uk_share_links_owner_target; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uk_share_links_owner_target ON public.share_links USING btree (created_by, target_type, target_key);


--
-- Name: uk_share_links_token; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uk_share_links_token ON public.share_links USING btree (token);


--
-- Name: ux_creation_favorites_user_creation; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ux_creation_favorites_user_creation ON public.creation_favorites USING btree (creation_id, user_id);


--
-- Name: ux_creation_likes_fingerprint; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ux_creation_likes_fingerprint ON public.creation_likes USING btree (creation_id, fingerprint) WHERE (fingerprint IS NOT NULL);


--
-- Name: ux_creation_likes_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ux_creation_likes_user_id ON public.creation_likes USING btree (creation_id, user_id) WHERE (user_id IS NOT NULL);


--
-- Name: conversation_messages conversation_messages_conversation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversation_messages
    ADD CONSTRAINT conversation_messages_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES public.conversations(id);


--
-- Name: conversation_states conversation_states_conversation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversation_states
    ADD CONSTRAINT conversation_states_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES public.conversations(id);


--
-- Name: creation_favorites creation_favorites_creation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.creation_favorites
    ADD CONSTRAINT creation_favorites_creation_id_fkey FOREIGN KEY (creation_id) REFERENCES public.creations(id);


--
-- Name: creation_likes creation_likes_creation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.creation_likes
    ADD CONSTRAINT creation_likes_creation_id_fkey FOREIGN KEY (creation_id) REFERENCES public.creations(id);


--
-- Name: credit_allocations credit_allocations_subscription_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.credit_allocations
    ADD CONSTRAINT credit_allocations_subscription_id_fkey FOREIGN KEY (subscription_id) REFERENCES public.subscriptions(id);


--
-- Name: subscriptions subscriptions_plan_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subscriptions
    ADD CONSTRAINT subscriptions_plan_id_fkey FOREIGN KEY (plan_id) REFERENCES public.subscription_plans(id);


--
-- PostgreSQL database dump complete
--

